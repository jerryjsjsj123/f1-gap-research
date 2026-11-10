"""One Research: a Lead that plans, Researchers that each investigate a Gap Phase in parallel, a Writer.

The Lead picks the Gap Phases worth investigating and calls `research` once per phase in the same
turn, so the Researchers run side by side, each in a fresh context. It reads their findings, may go
back once for what's still unexplained, then hands over to the Writer, which sees only the question
and the findings. Code, not prompts, enforces the caps.
"""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field

import litellm
from agents import (
    Agent,
    ModelSettings,
    RunContextWrapper,
    RunHooks,
    Runner,
    function_tool,
    set_tracing_disabled,
)
from agents.exceptions import MaxTurnsExceeded
from agents.extensions.models.litellm_model import LitellmModel

import analysis
from analysis import EvidenceLog
from f1data import DataError, list_races, load_race

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# Vertex answers 429 "Resource exhausted" when the shared Gemini capacity is busy, usually for a few seconds.
BACKOFF_SECONDS = (2, 5, 10)
TRANSIENT = (litellm.RateLimitError, litellm.ServiceUnavailableError, litellm.InternalServerError,
             litellm.Timeout, litellm.APIConnectionError)


class PatientModel(LitellmModel):
    """A LiteLLM model that waits out a busy Vertex, then falls back to another model if it stays busy."""

    def __init__(self, model: str, fallback: str | None = None):
        # LiteLLM routes the call to Gemini on Vertex AI. api_key is unused: Vertex uses Google credentials.
        super().__init__(model=model, api_key="unused")
        self.fallback = LitellmModel(model=fallback, api_key="unused") if fallback else None

    async def get_response(self, *args, **kwargs):
        for delay in BACKOFF_SECONDS:
            try:
                return await super().get_response(*args, **kwargs)
            except TRANSIENT:
                await asyncio.sleep(delay)
        if self.fallback:
            try:
                return await super().get_response(*args, **kwargs)
            except TRANSIENT:
                return await self.fallback.get_response(*args, **kwargs)
        return await super().get_response(*args, **kwargs)


MODEL = PatientModel("vertex_ai/gemini-3.5-flash", fallback="vertex_ai/gemini-3.5-flash-lite")
# Researchers read tool results and take notes: the cheaper model is enough.
RESEARCHER_MODEL = PatientModel("vertex_ai/gemini-3.5-flash-lite", fallback="vertex_ai/gemini-3.5-flash")
# A stalled model call would hang the page: give up after 60 s. PatientModel does the retrying.
_vertex = {"vertex_location": "global", "timeout": 60, "num_retries": 0}
if os.environ.get("GOOGLE_CLOUD_PROJECT"):
    _vertex["vertex_project"] = os.environ["GOOGLE_CLOUD_PROJECT"]
SETTINGS = ModelSettings(extra_args=_vertex)
# Researchers think a little before each step; the Writer mostly arranges what it was given.
RESEARCHER_SETTINGS = ModelSettings(extra_args={**_vertex, "reasoning_effort": "low"})
WRITER_SETTINGS = ModelSettings(extra_args={**_vertex, "reasoning_effort": "low"})

# One Research gets this many Researchers in all, over at most this many rounds of fan-out.
MAX_RESEARCHERS = 4
MAX_ROUNDS = 2  # the first fan-out, and one more for what it left unexplained
FIRST_ROUND_RESEARCHERS = 3  # so one is always left for the second round
LEAD_MAX_TURNS = 8
RESEARCHER_MAX_TURNS = 8
# Graders wait five minutes for an answer. Stop researching well before that and write up what we have.
RESEARCH_SECONDS = 170


@dataclass
class Session:
    """One conversation. Follow-up questions reuse its Race, Driver Pair and Evidence."""

    log: EvidenceLog = field(default_factory=EvidenceLog)
    turns: list[dict] = field(default_factory=list)  # what the user and the assistant said
    focus: tuple[int, str, str] | None = None  # round, driver A, driver B
    findings: list[str] = field(default_factory=list)  # every Researcher's report so far
    last_report: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class Run:
    """One Research's state, shared by every agent and tool in it. The model never sees it."""

    session: Session
    emit: Callable[[dict], None]
    tool_calls: list[dict] = field(default_factory=list)
    researchers: int = 0
    lead_turn: int = 0
    research_turns: set[int] = field(default_factory=set)
    findings: list[str] = field(default_factory=list)
    investigated: list[dict] = field(default_factory=list)
    overview: dict | None = None


@dataclass
class Desk:
    """A Researcher's view of the run: the shared state, plus its own name for the record."""

    run: Run
    name: str
    race_round: int


class LeadHooks(RunHooks):
    """Counts the Lead's turns, so `research` can tell one round of fan-out from the next."""

    async def on_llm_start(self, context, agent, system_prompt, input_items):
        if agent.name == "Lead":
            context.context.lead_turn += 1
            context.context.emit({"type": "status", "agent": "Lead", "text": "thinking"})


def _record(run: Run, agent: str, name: str, args: dict, call: Callable[[], dict]) -> str:
    """Run a tool, keep the record for /chat's tool_calls, and tell the page."""
    try:
        result = call()
    except DataError as e:
        # The model cannot see an exception. Return something it can reason about.
        result = {"error": str(e)}
    text = json.dumps(result, ensure_ascii=False)
    run.tool_calls.append({"agent": agent, "name": name, "args": args, "result": text})
    run.emit({"type": "tool", "agent": agent, "name": name, "args": args,
              "evidence_id": result.get("evidence_id"), "error": result.get("error")})
    return text


# --- The Researcher: one Gap Phase, with the analysis tools ---


@function_tool
def lap_pace(ctx: RunContextWrapper[Desk], driver_a: str, driver_b: str, lap_start: int, lap_end: int) -> str:
    """Compare two drivers' lap times over a range of laps: mean pace on clean laps, sector times,
    whose lap times drifted, and how much of the Gap change came on clean laps versus pit or neutralised laps.

    Args:
        driver_a: Three-letter code of the driver the Gap is measured for, e.g. 'VER'.
        driver_b: Three-letter code of the reference driver.
        lap_start: First lap of the range.
        lap_end: Last lap of the range.
    """
    desk = ctx.context
    args = {"driver_a": driver_a, "driver_b": driver_b, "lap_start": lap_start, "lap_end": lap_end}
    return _record(desk.run, desk.name, "lap_pace", args,
                   lambda: analysis.lap_pace(load_race(desk.race_round), driver_a, driver_b, lap_start, lap_end, desk.run.session.log))


@function_tool
def tyres(ctx: RunContextWrapper[Desk], driver_a: str, driver_b: str, lap_start: int, lap_end: int) -> str:
    """Which tyre compound each driver was on over a range of laps, and how many laps old the tyres were.

    Args:
        driver_a: Three-letter driver code.
        driver_b: Three-letter driver code.
        lap_start: First lap of the range.
        lap_end: Last lap of the range.
    """
    desk = ctx.context
    args = {"driver_a": driver_a, "driver_b": driver_b, "lap_start": lap_start, "lap_end": lap_end}
    return _record(desk.run, desk.name, "tyres", args,
                   lambda: analysis.tyres(load_race(desk.race_round), driver_a, driver_b, lap_start, lap_end, desk.run.session.log))


@function_tool
def pit_stops(ctx: RunContextWrapper[Desk], driver_a: str, driver_b: str, lap_start: int, lap_end: int) -> str:
    """Each driver's pit stops in a range of laps: pit lane time, stationary time, in-lap and out-lap,
    what each stop cost against the driver's own pace, and whether it was taken under a Safety Car.

    Args:
        driver_a: Three-letter driver code.
        driver_b: Three-letter driver code.
        lap_start: First lap of the range.
        lap_end: Last lap of the range.
    """
    desk = ctx.context
    args = {"driver_a": driver_a, "driver_b": driver_b, "lap_start": lap_start, "lap_end": lap_end}
    return _record(desk.run, desk.name, "pit_stops", args,
                   lambda: analysis.pit_stops(load_race(desk.race_round), driver_a, driver_b, lap_start, lap_end, desk.run.session.log))


@function_tool
def race_events(ctx: RunContextWrapper[Desk], driver_a: str, driver_b: str, lap_start: int, lap_end: int) -> str:
    """What race control did over a range of laps: Safety Car, Virtual Safety Car, red and yellow flags,
    penalties and investigations involving either driver, blue flags, and the weather.

    Args:
        driver_a: Three-letter driver code.
        driver_b: Three-letter driver code.
        lap_start: First lap of the range.
        lap_end: Last lap of the range.
    """
    desk = ctx.context
    args = {"driver_a": driver_a, "driver_b": driver_b, "lap_start": lap_start, "lap_end": lap_end}
    return _record(desk.run, desk.name, "race_events", args,
                   lambda: analysis.race_events(load_race(desk.race_round), driver_a, driver_b, lap_start, lap_end, desk.run.session.log))


@function_tool
def track_position(ctx: RunContextWrapper[Desk], driver_a: str, driver_b: str, lap_start: int, lap_end: int) -> str:
    """Where each driver was running over a range of laps: race position, which car was directly ahead
    on track and how close (traffic), and the overtakes involving either driver.

    Args:
        driver_a: Three-letter driver code.
        driver_b: Three-letter driver code.
        lap_start: First lap of the range.
        lap_end: Last lap of the range.
    """
    desk = ctx.context
    args = {"driver_a": driver_a, "driver_b": driver_b, "lap_start": lap_start, "lap_end": lap_end}
    return _record(desk.run, desk.name, "track_position", args,
                   lambda: analysis.track_position(load_race(desk.race_round), driver_a, driver_b, lap_start, lap_end, desk.run.session.log))


SUPPORT_LEVELS = """\
Support Levels:
- Supported: a tool result shows it directly and it accounts for the seconds. Examples: one driver's pit
  stop cost 3.1s more than the other's; one driver made a stop the other did not; a Safety Car was out
  on those laps and the Gap closed up behind it; one driver started 16 grid slots further back.
- Consistent: the data fits this explanation but cannot prove it caused the change. Tyre age, tyre
  compound and traffic are never more than Consistent, because the data has no grip, wear or
  "could have gone faster" measurement. A reason behind a Supported fact (why a stop was slow, why a
  team chose a strategy) is also at most Consistent.
- Unanswerable: the data has nothing on it. Driver mistakes, car damage, setup, team orders, fuel load,
  engine modes, tyre temperatures and a driver deliberately managing pace are all Unanswerable: this
  dataset has lap times, tyre stints, pit stops, race control messages, positions and weather, nothing else.

How races work, for reading the data:
- Under a Safety Car every car queues up behind it, so the gaps between cars shrink to a second or
  two whatever they were before. A Gap that closes on Safety Car laps is explained by the Safety Car.
  Under a Virtual Safety Car everyone slows by the same amount and gaps stay roughly frozen.
- A pit stop costs about 20 to 25 seconds. When one driver stops and the other has not yet, the Gap
  swings by that much and swings back when the other stops: only the net change over both stops
  is a real gain or loss.
- A stop under a Safety Car or Virtual Safety Car costs much less than one at racing speed."""

researcher = Agent(
    name="Researcher",
    instructions=f"""\
You investigate one range of laps of a 2026 Formula 1 race for a Driver Pair, to explain why the Gap
between them changed over those laps. The Gap is driver A's time at the line minus driver B's:
positive means A is behind B, and a positive change means A lost time to B.

1. Call lap_pace for the range first. It tells you how much of the change came on clean racing laps
   and how much on pit or neutralised laps.
2. Follow what it shows: pit_stops when a stop falls in the range, tyres when pace or drift differs,
   race_events for Safety Cars, flags, penalties and rain, track_position for traffic and overtakes.
   You may compare either driver with a third car, e.g. the car they were stuck behind.
   Make at most 6 tool calls in all.
3. Reply in this form and nothing else:

   Laps <first>-<last>: <A> lost <n>s to <B> (or gained <n>s on <B>) [E#]
   What the data shows:
   - <a fact with its numbers> [E#]
   - ... (3 to 6 facts)
   Explanations:
   - [Supported|Consistent|Unanswerable] <a cause>: about <n>s of the change. <why you rate it so> [E#]
   - ... (1 to 4, the one that accounts for most seconds first)
   Open question: <what the data you saw leaves unexplained, or "none">

Rules:
- An Explanation names a cause: a pit stop, tyre age or compound, traffic, a Safety Car, a penalty,
  rain, a grid position. A pace difference on its own is a fact, not a cause. When tyres, traffic and
  race events do not account for a pace difference, write an Unanswerable line saying the data
  cannot tell why one car was faster: it cannot separate the car from the driver.
- Every number you quote must come from a tool result, followed by that result's evidence_id in
  square brackets, e.g. [E7]. For a fact the task gave you, cite the id the task gave with it.
  Never cite an id you did not receive.
- Use only the tool results. You know nothing else about this race: it happened after your training.
- Do not explain every slow lap with tyre wear. If nothing in the data accounts for a change, say so.

{SUPPORT_LEVELS}""",
    model=RESEARCHER_MODEL,
    model_settings=RESEARCHER_SETTINGS,
    tools=[lap_pace, tyres, pit_stops, race_events, track_position],
)


# --- The Lead: clarifies, plans, fans out, checks for gaps ---


@function_tool
def race_overview(ctx: RunContextWrapper[Run], round: int) -> str:
    """Get one race's field: every driver's team, grid slot, finishing position and status, plus when
    the race was neutralised. Use it to resolve driver names and to suggest who to compare a driver with.

    Args:
        round: The race's round number from the season list.
    """
    run = ctx.context
    return _record(run, "Lead", "race_overview", {"round": round},
                   lambda: analysis.race_overview(load_race(round), run.session.log))


@function_tool
def gap_overview(ctx: RunContextWrapper[Run], round: int, driver_a: str, driver_b: str) -> str:
    """Rebuild the Gap between two drivers over a whole race and split it into candidate Gap Phases.
    Also fixes the Race and Driver Pair that `research` will investigate.

    Args:
        round: The race's round number from the season list.
        driver_a: Three-letter code of the driver the user is asking about (the Gap is positive when this driver is behind).
        driver_b: Three-letter code of the driver to compare with.
    """
    run = ctx.context

    def call() -> dict:
        race = load_race(round)
        result = analysis.gap_overview(race, driver_a, driver_b, run.session.log)
        run.session.focus = (round, result["a"], result["b"])
        run.overview = result
        return result

    return _record(run, "Lead", "gap_overview", {"round": round, "driver_a": driver_a, "driver_b": driver_b}, call)


@function_tool
async def research(ctx: RunContextWrapper[Run], task: str, lap_start: int, lap_end: int) -> str:
    """Send a Researcher to explain why the Gap changed over one range of laps. Returns its findings,
    each Explanation with a Support Level and the Evidence behind it. Call gap_overview first.

    Args:
        task: What to find out about these laps, and anything from the overview worth knowing.
        lap_start: First lap to investigate.
        lap_end: Last lap to investigate.
    """
    run = ctx.context
    args = {"task": task, "lap_start": lap_start, "lap_end": lap_end}
    if run.session.focus is None:
        return _record(run, "Lead", "research", args, lambda: {"error": "Call gap_overview first to fix the Race and Driver Pair."})
    if run.lead_turn not in run.research_turns and len(run.research_turns) >= MAX_ROUNDS:
        return _record(run, "Lead", "research", args, lambda: {"error": "No research rounds left. Reply DONE."})
    if run.researchers >= MAX_RESEARCHERS:
        return _record(run, "Lead", "research", args, lambda: {"error": "No Researchers left. Reply DONE."})
    first_round = not run.research_turns or run.research_turns == {run.lead_turn}
    if first_round and run.researchers >= FIRST_ROUND_RESEARCHERS:
        return _record(run, "Lead", "research", args, lambda: {
            "error": f"The first round is limited to {FIRST_ROUND_RESEARCHERS} Researchers. Read their findings; "
                     "you may send one more afterwards if something is still unexplained."})
    run.research_turns.add(run.lead_turn)
    run.researchers += 1
    name = f"Researcher {run.researchers}"
    race_round, a, b = run.session.focus
    race = load_race(race_round)
    round_number = len(run.research_turns)
    run.investigated.append({"agent": name, "lap_start": lap_start, "lap_end": lap_end, "round": round_number})
    run.emit({"type": "researcher_start", "agent": name, "task": task, "lap_start": lap_start, "lap_end": lap_end,
              "round": round_number})

    # What researcher.as_tool() does: run the Researcher in a fresh context and return its answer.
    # Written out so each one gets a numbered name, its own record of tool calls, and progress events.
    prompt = (
        f"Race: {race.label}, {race.info['total_laps']} laps.\n"
        f"Driver Pair: A = {a}, B = {b}.\n"
        f"Laps to investigate: {lap_start}-{lap_end}.\n"
        f"Task: {task}"
    )
    try:
        findings = ""
        for _ in range(2):  # the model now and then returns an empty answer: ask once more
            result = await Runner.run(researcher.clone(name=name), prompt, context=Desk(run, name, race_round),
                                      max_turns=RESEARCHER_MAX_TURNS)
            findings = str(result.final_output or "").strip()
            if findings:
                break
        findings = findings or f"Laps {lap_start}-{lap_end}: the Researcher returned nothing."
    except MaxTurnsExceeded:
        findings = f"Laps {lap_start}-{lap_end}: the Researcher ran out of turns before reporting."
    except Exception as e:  # one Researcher failing must not sink the others
        findings = f"Laps {lap_start}-{lap_end}: the Researcher failed ({type(e).__name__}: {str(e)[:200]})."
    run.findings.append(f"{name}, laps {lap_start}-{lap_end}. Task: {task}\n\n{findings}")
    run.emit({"type": "researcher_done", "agent": name, "findings": findings})
    run.tool_calls.append({"agent": "Lead", "name": "research", "args": args, "result": findings})
    return findings


def _season() -> str:
    return "\n".join(
        f"- round {race['round']}: {race['name']} ({race['location']}, {race['country']}), {race['date']}, "
        f"{race['total_laps']} laps" for race in list_races()
    )


LEAD_INSTRUCTIONS = f"""\
You lead a research team that explains how the Gap between two Formula 1 drivers was built in one race
of the 2026 season. Your team works only from race data; you know nothing else about these races, which
happened after your training. The Gap is driver A's time at the line minus driver B's at the end of
each lap: positive means A is behind.

The races you have data for (2026 season, races only):
{_season()}

Step 1. Decide whether you can start.
- You need one Race from the list and two drivers who took part in it. If the user's message, or the
  session focus given with it, settles all three, go to step 2.
- If the race is missing or ambiguous, ask which race, offering up to three likely ones from the list.
- If both drivers are clear, you do not need race_overview.
- If only one driver is named, call race_overview and ask who to compare them with, offering two or
  three candidates with a reason each: their teammate, the driver who finished directly ahead or
  behind, or the driver who started alongside them. Do not pick for the user.
- If the user asks for something outside the data (qualifying, sprints, practice, other seasons,
  predictions, telemetry, team radio, championship standings), say that this assistant only explains
  the Gap between two drivers in a 2026 race, and suggest a question it can answer.
- When you ask or decline, reply in the language the user wrote in (English for an English message,
  whatever country the race was in), briefly, and stop.
  Do not start research on a guess.

Step 2. Plan. Call gap_overview with A = the driver the user is asking about. Read the candidate Gap
Phases and pick the lap ranges that matter for the user's question:
- For a general "why" question, the two or three ranges where the Gap changed most. A range is one
  candidate phase, or a few neighbouring ones that tell one story (a pit cycle and the laps around
  it). Do not cut the race into equal parts, and leave out phases that moved the Gap by under about
  1.5s unless the question is about them.
- If the user asked about one thing (the start, pit strategy, a Safety Car, the final stint), only the
  ranges that bear on it.

Step 3. Fan out. Call research once per range, all in the same turn, so the Researchers work in
parallel: two or three in this first round ({FIRST_ROUND_RESEARCHERS} at most). You have {MAX_RESEARCHERS} Researchers in all.
Give each a task that says what to find out and what the overview already shows about those laps,
with the overview's evidence_id so the Researcher can cite it, e.g. "VER lost 5.7s on laps 58-61 [E2]".

Step 4. Check for gaps. Read the findings. If a large change is still unexplained, or a finding raises a
question that the data could answer (for example, a driver lost time behind another car: why was that
car there?), call research once more for just that. You get one such second round. If the findings
cover the question, skip it.

Step 5. Reply DONE and nothing else. A Writer turns the findings into the report.

For a follow-up question in the same conversation, keep the session's Race and Driver Pair unless the
user changes them, and research only what the follow-up asks."""

lead = Agent(
    name="Lead",
    instructions=LEAD_INSTRUCTIONS,
    model=MODEL,
    model_settings=SETTINGS,
    tools=[race_overview, gap_overview, research],
)


# --- The Writer: one call, after the research ends ---

writer = Agent(
    name="Writer",
    instructions=f"""\
You write a short report that explains how the Gap between two Formula 1 drivers was built, for a fan
who watched the race but has not looked at the data. Use only the overview and findings you are given.

Write in the language of the user's question. Use this structure, in markdown:

**Bottom line.** Two or three sentences: the final Gap in plain words ("X finished 14.6s ahead of Y"),
and the one or two phases where most of it was built.

### Where the gap came from
A table with the columns: Laps | What happened | Gap change | Main explanation | Support.
Never more than 5 rows, in race order. Rows are for laps a Researcher investigated: merge neighbouring
laps into one row to stay within 5, split a range only where it holds two different stories, and
add no rows for laps nobody investigated. "Gap change" is in seconds and says who lost, e.g. "VER lost 5.7s". "Support" is the Support
Level of that row's main Explanation.

### Phase by phase
One short subsection per table row (#### Laps a-b: a plain title), two to four sentences each: what
the data shows, then each Explanation with its Support Level in bold, e.g. **Consistent:** ...

### What the data can't tell you
Two to four bullets: what stayed Unanswerable or unexplained, phases nobody investigated that moved
the Gap by more than 2s, and any limit of the Gap noted in the overview.

Rules:
- End every sentence that states a fact or number with the Evidence it came from, e.g. [E4] or [E4, E7].
  Use only ids that appear in the overview or the findings. Never invent one. Only E-numbers are
  Evidence: lap ranges and phases are not citations.
- Write the Support Levels in English in every language: Supported, Consistent, Unanswerable.
- Keep each Explanation's Support Level as the Researcher gave it. Do not upgrade one, and do not turn
  an observation ("was slower on clean laps") into a cause.
- Keep each number with the meaning its finding gave it. Round to one decimal.
- When each driver made a stop in the laps you describe, report the net change over both stops, not
  one driver's stop as a loss.
- Say "lost time to" and "gained time on" rather than quoting signed numbers in prose.
- Plain, specific sentences. No "crucial", "pivotal", "masterclass", "it is worth noting".
- The whole report stays under 450 words, not counting the table.
- For a follow-up question, answer only that question: a direct answer first, then the evidence,
  under 200 words. Skip the sections that don't apply.

{SUPPORT_LEVELS}""",
    model=MODEL,
    model_settings=WRITER_SETTINGS,
)


def _overview_for_writer(overview: dict | None) -> str:
    if not overview:
        return "(no new overview for this question)"
    keep = {key: value for key, value in overview.items() if key != "reading"}
    return json.dumps(keep, ensure_ascii=False)


async def _write(brief: str) -> str:
    """Run the Writer. The research is already paid for, so a failed call is worth one more try."""
    try:
        return str((await Runner.run(writer, brief)).final_output)
    except Exception:
        await asyncio.sleep(5)
        return str((await Runner.run(writer, brief)).final_output)


async def run_research(session: Session, message: str, emit: Callable[[dict], None]) -> dict:
    """Answer one user message. Returns the reply, every tool call made, and what the page draws."""
    run = Run(session=session, emit=emit)
    prompt = message
    if session.focus:
        race_round, a, b = session.focus
        prompt = (
            f"[Session focus: round {race_round}, Driver Pair A = {a}, B = {b}. "
            f"Evidence so far: E1-E{len(session.log.items)}.]\n\n{message}"
        )
    conversation = session.turns + [{"role": "user", "content": prompt}]

    emit({"type": "status", "agent": "Lead", "text": "reading the question"})
    reply = ""
    try:
        result = await asyncio.wait_for(
            Runner.run(lead, conversation, context=run, hooks=LeadHooks(), max_turns=LEAD_MAX_TURNS),
            timeout=RESEARCH_SECONDS,
        )
        reply = str(result.final_output or "").strip()
    except (MaxTurnsExceeded, asyncio.TimeoutError):
        # Out of turns or time. If Researchers reported, write up what they found.
        if not run.findings:
            raise

    wrote_report = bool(run.findings) or reply.upper().startswith("DONE")
    if wrote_report:
        emit({"type": "status", "agent": "Writer", "text": "writing the report"})
        parts = [f"Question: {message}", f"Overview:\n{_overview_for_writer(run.overview)}"]
        earlier = [finding for finding in session.findings if finding not in run.findings]
        if session.last_report:
            parts.append(
                "This is a follow-up question. Your earlier report in this conversation:\n" + session.last_report
            )
        if earlier:
            parts.append("Findings from earlier in this conversation:\n\n" + "\n\n---\n\n".join(earlier))
        parts.append("New findings:\n\n" + ("\n\n---\n\n".join(run.findings) or "(none)"))
        brief = "\n\n".join(parts)
        report = await _write(brief)

        # A report may only cite Evidence that exists. Give the Writer one chance to fix it, then cut.
        unknown = analysis.check_citations(report, session.log)["unknown"]
        if unknown:
            emit({"type": "status", "agent": "Writer", "text": "fixing citations"})
            fix = f"{brief}\n\nYour draft cited Evidence that does not exist: {', '.join(unknown)}. Rewrite it using only real ids.\n\nDraft:\n{report}"
            report = await _write(fix)
        reply = analysis.drop_unknown_citations(report, session.log)
        session.findings.extend(run.findings)
        session.last_report = reply
    else:
        reply = analysis.drop_unknown_citations(reply, session.log) or "I couldn't produce an answer. Please try rephrasing."

    session.turns += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
    cited = analysis.check_citations(reply, session.log)["cited"]
    return {
        "response": reply,
        "tool_calls": run.tool_calls,
        "kind": "report" if wrote_report else "reply",
        "chart": _chart(session, run) if wrote_report else None,
        "evidence": {evidence_id: session.log.get(evidence_id) for evidence_id in cited},
    }


def _chart(session: Session, run: Run) -> dict | None:
    """What the page needs to draw the Gap: the lap-by-lap series, the phases, stops and neutralisations."""
    if session.focus is None:
        return None
    race_round, a, b = session.focus
    race = load_race(race_round)
    phases = analysis.gap_phases(race, a, b)
    drivers = {}
    for code in (a, b):
        driver = race.driver(code)
        drivers[code] = {
            "name": driver["name"],
            "team": driver["team"],
            "colour": driver.get("team_colour"),
            "grid": driver.get("grid"),
            "finish": driver.get("position"),
            "pit_laps": sorted(stop["lap"] for stop in race.pits if stop["driver"] == driver["number"] and stop["lap"]),
        }
    return {
        "race": race.label,
        "a": a,
        "b": b,
        "drivers": drivers,
        "points": phases["points"],
        "final_gap": phases["final_gap"],
        "official_margin": (run.overview or {}).get("official_margin"),
        "phases": [{key: phase[key] for key in ("id", "lap_start", "lap_end", "kind", "change")} for phase in phases["phases"]],
        "neutralisations": [
            {"kind": period["kind"], "lap_start": period["lap_start"], "lap_end": period["lap_end"]}
            for period in analysis.neutralisations(race)
        ],
        "investigated": run.investigated,
    }
