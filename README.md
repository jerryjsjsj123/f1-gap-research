# F1 Gap Research

**Faster driver, smarter pit wall, or lucky timing?**

Two drivers start a race side by side. Ninety minutes later, one finishes 25 seconds ahead. Where did those 25 seconds come from? Was he simply faster on track, did his team make a better call in the pits, or did a Safety Car arrive at exactly the right moment for him?

Commentators always have a story. This agent checks the story against the data. It rebuilds a 2026 Formula 1 race lap by lap for two drivers, finds the laps where the gap between them actually moved, sends researchers to investigate each of those stretches in parallel, and reports which explanations the data supports and which ones it cannot confirm.

- **Deployed at:** https://f1-gap-research-git-1062559193366.europe-west1.run.app (Columbia accounts only)
- **Path:** Deep Research
- **Target user:** an F1 fan who watched the race and wants to know how a result came about, but has neither the time nor the tooling to line up lap times, tyre stints and pit stops for two cars.

## How it works

```
question ──► Lead ──► gap_overview: the Gap lap by lap, split into candidate Gap Phases
              │
              ├──► Researcher 1 (laps a-b) ┐  in parallel, each in a fresh context,
              ├──► Researcher 2 (laps c-d) ├─ each with the five analysis tools
              ├──► Researcher 3 (laps e-f) ┘
              │
              ├──► reads the findings; if something is still unexplained,
              │    sends one more Researcher (second round)
              ▼
            Writer ──► report with a citation on every fact ──► citation check
```

Three agent shapes with different jobs:

| Agent | Model | Job | Tools |
|---|---|---|---|
| **Lead** | `gemini-3.5-flash` | Decides whether the question is answerable, asks back if it is vague, plans which laps to investigate, fans out, checks the findings for holes | `race_overview`, `gap_overview`, `research` |
| **Researcher** (up to 4) | `gemini-3.5-flash-lite` | Explains why the Gap changed over one range of laps; rates every explanation | `lap_pace`, `tyres`, `pit_stops`, `race_events`, `track_position` |
| **Writer** | `gemini-3.5-flash` | Turns the findings into a short report. Sees only the question, the overview and the findings | none |

What each requirement of the Deep Research path looks like here:

- **Parallel fan-out in fresh contexts.** The Lead calls `research` several times in one turn. Each call starts a Researcher with a new context that contains only its task. The research log on the page shows them working side by side.
- **Going back for more.** After reading the findings the Lead may send one more Researcher at whatever is still unexplained. Code enforces the caps: 4 Researchers per question, 3 in the first round, 2 rounds, 8 turns per agent, and a 170 second budget before the Writer takes over with what there is.
- **Vague questions get a question back.** A question needs one race and two drivers. If the race is missing, or only one driver is named, the Lead asks, and offers candidates it looked up (the teammate, the driver who finished directly ahead). It does not guess.
- **Accurate citations.** Every tool call registers an Evidence record (`E1`, `E2`, ...) holding the source, race, drivers, lap range and the data rows. The Writer must end every factual sentence with the Evidence it rests on. After writing, the program checks that every cited id exists; the Writer gets one chance to fix a bad citation, and any that remain are removed. On the page, each citation opens the rows behind it.
- **Saying what the data can't answer.** Every explanation carries a Support Level: **Supported** (the data shows it directly), **Consistent** (the data fits, but cannot prove it), or **Unanswerable** (the data has nothing on it). Driver mistakes, car damage, team orders, fuel and engine modes are not in the data, so they are always Unanswerable. Tyres and traffic can never be more than Consistent.
- **The model does no arithmetic.** Gaps, pace differences, pit stop costs and tyre ages are computed in `analysis.py`. Tool results also state the key numbers in words ("VER lost 3.1s to NOR"), so the model never has to interpret a sign.

Follow-up questions in the same conversation keep the race and the two drivers and trigger a narrower investigation.

## Data

Two public sources, combined:

| Source | What it provides | Endpoints |
|---|---|---|
| [OpenF1](https://openf1.org) | Lap times and sector times, tyre stints, pit stops, race-control messages, overtakes, positions, weather, driver list | `/v1/laps`, `/v1/stints`, `/v1/pit`, `/v1/race_control`, `/v1/overtakes`, `/v1/position`, `/v1/weather`, `/v1/drivers`, `/v1/session_result` |
| [Jolpica](https://github.com/jolpica/jolpica-f1) (the Ergast successor) | Starting grid, classified finishing position, status | `/ergast/f1/2026/{round}/results.json` |

Coverage: the 20 races of the 2026 season run so far, from Australia (8 March) to Brazil (8 November). The season is after the model's training cutoff; asked without tools, the model says it has no information about these races (see the comparisons below).

**The data ships with the app.** `scripts/build_snapshot.py` downloads every completed race and writes `data/races.json` (one entry per race) and `data/races/NN.json.gz` (that race's tables), about 0.9 MB in total. The deployed agent reads only these files and never calls the APIs, so it is not affected by OpenF1's rate limit (30 requests a minute) or by an outage during grading. To load or refresh the data:

```bash
uv run scripts/build_snapshot.py           # adds races that are not in the snapshot yet (about 8 minutes from empty)
uv run scripts/build_snapshot.py --force   # downloads everything again
```

**How the Gap is defined.** The Gap is driver A's time at the finish line minus driver B's at the end of each lap, so a positive Gap means A is behind. It is derived from OpenF1's lap start timestamps. Those timestamps are approximate: the Gap at the flag is within about 0.2 s of the official margin, and the report shows both.

**What the data cannot show.** There is no telemetry, no team radio, no tyre temperature or wear, no fuel load and no record of driver errors or car damage. Time penalties applied after the flag are not in the Gap. Qualifying, sprints and practice are out of scope.

## Sample queries

Each sample shows the answer to expect and where it comes from, then the agent's answer, then the answer of the same model (`gemini-3.5-flash`) with no tools and no system prompt. The expected figures were computed directly from the snapshot files, independently of the agent's tools.

### 1. British Grand Prix (round 9), Hamilton vs Leclerc

**Question:** Hamilton was well behind Leclerc for most of the British Grand Prix. How did he finish less than a second behind?

**Expected answer:** Leclerc led Hamilton by 20.2 s at the end of lap 47. A Safety Car was deployed on lap 48 and stayed out until lap 52; both drivers pitted on lap 48 (pit lane time 28.7 s for Hamilton, 28.9 s for Leclerc). The Gap fell from 20.2 s to 0.7 s over laps 48-52 (official margin 0.772 s). The cause is the Safety Car bunching the field, not pace.

**Where it comes from:** OpenF1 `/v1/laps`, `/v1/pit`, `/v1/race_control` with `session_key=11326`; Jolpica `/ergast/f1/2026/9/results.json`

<details>
<summary><b>The agent's answer</b> (7 tool calls, 6 Evidence records cited)</summary>

> **Bottom line.** Hamilton finished 0.7s behind Leclerc [E1]. Leclerc held a 20.2s lead by lap 47, but this gap collapsed during a late-race Safety Car period [E1, E2].
>
> ### Where the gap came from
>
> | Laps | What happened | Gap change | Main explanation | Support |
> | :--- | :--- | :--- | :--- | :--- |
> | 48-52 | Safety Car neutralisation and pit stops | HAM gained 19.5s | The Safety Car bunched the field | Supported |
>
> ### Phase by phase
>
> #### Laps 48-52: Late Safety Car and tyre changes
> Hamilton gained 19.5s on Leclerc entirely during these neutralised laps [E2]. Both drivers pitted on lap 48 to switch to Soft tyres, with Hamilton spending 28.7s in the pit lane and Leclerc spending 28.9s [E4, E5].
> **Supported:** The Safety Car bunched up the field, causing Leclerc's 20.2s lead to collapse [E2, E3, E4].
> **Consistent:** Pitting under the Safety Car minimised the time lost for both drivers compared to green-flag pit stops [E4].
> **Unanswerable:** Russell overtook Hamilton and Hamilton was investigated for a yellow flag infringement on lap 49, but the data cannot quantify any performance effects under Safety Car conditions [E3, E6].
>
> ### What the data can't tell you
> * Why Hamilton lost a total of 20.2s over laps 1 to 47, as these green-flag and VSC phases were not investigated [E1].
> * Whether driver mistakes, car setup, or tyre management influenced the gap before the Safety Car was deployed.

</details>

<details>
<summary><b>The same model with no tools and no system prompt</b></summary>

> There is a slight mix-up in either the **location of the Grand Prix** or the **final finishing order**, as Lewis Hamilton and Charles Leclerc have had two famous battles that match your description. 
>
> Here is how those two scenarios played out:
>
> ---
>
> ### Scenario 1: The 2019 Belgian Grand Prix (The "B" Grand Prix Mix-up)
> If you are thinking of a race where Hamilton was well behind Leclerc and finished **less than a second behind him**, you are likely thinking of the **2019 Belgian Grand Prix** (both races start with "B"). 
>
> * **The Setup:** Charles Leclerc led almost the entire race from pole position. Hamilton was running in 2nd but was well behind, trailing Leclerc by over 6 seconds after the pit stops.
> * **The Chase:** In the final stint, Leclerc’s medium tires began to degrade rapidly, while Hamilton’s Mercedes was much better on tire wear. Hamilton began putting in blistering lap times, shrinking a massive gap in the closing laps.
> * **The Finish:** On the very last lap, Hamilton got within DRS range of Leclerc. Leclerc just managed to hold on to take his first-ever Formula 1 victory, with Hamilton finishing **0.981 seconds behind him**.
>
> ---
>
> ### Scenario 2: The 2021 British Grand Prix (The actual British GP, but Hamilton won)
> If you are thinking of the **British Grand Prix at Silverstone**, Hamilton was indeed well behind Leclerc for most of the race, but **Hamilton actually won**, and Leclerc finished behind him. 
>
> * **The Setup:** On Lap 1, Hamilton and Max Verstappen collided, sending Verstappen into the barrier. Hamilton was given a **10-second time penalty**. During the red flag and subsequent restart, Charles Leclerc took the lead in his Ferrari.
> * **The Penalty:** Hamilton served his 10-second penalty during his pit stop on Lap 27, which dropped him deep into the field, **well behind Leclerc** (who was leading comfortably).
> * **The Chase:** Hamilton unleashed a relentless charge. He overtook Lando Norris, was let past by teammate Valtteri Bottas, and began hunting down Leclerc at a rate of nearly a second per lap.
> * **The Finish:** With just three laps to go (Lap 50 of 52), Hamilton caught Leclerc at Copse corner (the same place he collided with Verstappen), forced Leclerc wide, and took the lead to win his home Grand Prix. Leclerc finished 2nd, 3.8 seconds behind Hamilton.
>
> ---
>
> ### Scenario 3: The 2022 British Grand Prix (The Safety Car)
> If you are thinking of the **2022 British Grand Prix**, Hamilton was well behind Leclerc (who was leading), but a late-race event brought them together.
>
> * **The Setup:** Leclerc was leading the race, and Hamilton was in 3rd, struggling to close the gap to the Ferraris. 
> * **The Turn of Events:** On Lap 39, Esteban Ocon retired, triggering a **Safety Car**. This bunched the entire pack together, completely erasing the massive gap Leclerc had built.
> * **The Finish:** After a chaotic restart where Carlos Sainz took the lead, Hamilton and Leclerc engaged in an epic wheel-to-wheel battle. Hamilton eventually finished 3rd, with Leclerc finishing 4th, just over two seconds behind him.

</details>

### 2. Spanish Grand Prix in Madrid (round 14), Russell vs Antonelli

**Question:** Where did Russell lose 30 seconds to Antonelli in the Spanish Grand Prix in Madrid?

**Expected answer:** Russell finished 29.9 s behind (official margin 29.829 s). He was 9.4 s behind after 5 laps (he started 6th on Hard tyres, Antonelli 2nd on Mediums). Both stopped on lap 14 under a Virtual Safety Car. Russell then made a second stop on lap 28 that Antonelli did not make, which moved the Gap from 11.7 s to 36.6 s (24.9 s). On fresher tyres Russell took back 6.8 s over laps 30-57.

**Where it comes from:** OpenF1 `/v1/laps`, `/v1/pit`, `/v1/stints` with `session_key=11369`; Jolpica `/ergast/f1/2026/14/results.json`

<details>
<summary><b>The agent's answer</b> (16 tool calls, 9 Evidence records cited)</summary>

> **Bottom line.** George Russell finished 29.9 seconds behind his teammate Kimi Antonelli [E1]. Most of this gap was built during the opening five laps and when Russell had to make a second pit stop [E1].
>
> ### Where the gap came from
>
> | Laps | What happened | Gap change | Main explanation | Support |
> | :--- | :--- | :--- | :--- | :--- |
> | 1-5 | Russell fell behind at the start and on early clean laps | RUS lost 9.4s | Starting tyre compound difference | Consistent |
> | 16-27 | Russell was slower during a green-flag stint | RUS lost 3.2s | Tyre compound and degradation | Unanswerable |
> | 28-29 | Russell made a second pit stop | RUS lost 24.9s | Russell's extra pit stop | Supported |
>
> ### Phase by phase
>
> #### Laps 1-5: The opening struggle
> Russell lost 9.4s to Antonelli over the first five laps [E4]. He lost 1.6s on the opening lap starting from 6th while Antonelli started 2nd [E4, E6]. Over the next four laps, Russell lost another 7.7s while running on Hard tyres compared to Antonelli's Mediums [E4, E11]. **Supported**: Starting four grid slots behind Antonelli contributed to the opening lap loss [E4, E6]. **Consistent**: Starting on the Hard compound explained Russell's pace deficit on the early clean laps [E4, E11].
>
> #### Laps 16-27: Green-flag pace deficit
> During this green-flag phase, Russell lost 3.2s to Antonelli over 12 clean laps [E2]. Russell was running on Medium tyres while Antonelli was on Hard tyres [E7]. **Unanswerable**: The data cannot determine if this pace deficit was due to tyre compounds, tyre degradation, or general driver and car performance [E2, E7].
>
> #### Laps 28-29: The second pit stop
> Russell lost 24.9s to Antonelli across these two laps [E1, E3]. This was because Russell stopped for a new set of Hard tyres at the end of lap 28, spending 30.6s in the pit lane [E5, E10]. **Supported**: Russell's second pit stop and subsequent out-lap cost 24.9s in total relative to Antonelli [E3, E5].
>
> ### What the data can't tell you
>
> * Between laps 30 and 57, Russell gained 6.8s on Antonelli, but this phase was not investigated [E1].
> * The exact impact of tyre wear and compound performance on Russell's pace from lap 16 to 27 remains unproven [E2, E7].
> * The data cannot show if Russell suffered from any car damage, engine mode restrictions, or setup issues [E2].

</details>

<details>
<summary><b>The same model with no tools and no system prompt</b></summary>

> This scenario has not actually happened, as several details in your question refer to events that have not yet taken place:
>
> 1. **The Spanish Grand Prix in Madrid hasn't happened yet:** The Spanish Grand Prix is currently held at the Circuit de Barcelona-Catalunya. A new street/exhibition circuit in Madrid (at the IFEMA venue) is scheduled to host the Spanish Grand Prix starting in **2026**. 
> 2. **Russell and Antonelli haven't raced each other yet:** George Russell and Andrea Kimi Antonelli have never competed against each other in an F1 race. Antonelli is scheduled to make his Formula 1 debut as Russell's Mercedes teammate in **2025**. 
>
> If you are thinking of a different track, Antonelli has participated in private testing for Mercedes (using older F1 cars) at various European tracks like Barcelona, Spa, and Imola, and he drove in Free Practice 1 at Monza and Mexico in 2024. However, there is no official record of Russell losing 30 seconds to him in any session. 
>
> It is also possible this scenario occurred in a virtual race, a simulated championship, or a video game (like *F1 24* or *iRacing*).

</details>

### 3. Miami Grand Prix (round 4), Russell vs Antonelli

**Question:** Why did Russell finish 43 seconds behind his teammate Antonelli in Miami?

**Expected answer:** Russell finished 42.9 s behind (official margin 43.051 s). The Gap was only 0.6 s when the Safety Car came in on lap 11. Russell stopped on lap 20 and Antonelli on lap 26; the Gap was 10.2 s after both stops. Over laps 28-57, both on Hard tyres, Russell was 1.1 s per lap slower and lost 32.7 s. Nothing in the data (tyres, stops, race control) accounts for that pace difference, so the agent should say its cause is Unanswerable.

**Where it comes from:** OpenF1 `/v1/laps`, `/v1/pit`, `/v1/stints`, `/v1/race_control` with `session_key=11280`; Jolpica `/ergast/f1/2026/4/results.json`

<details>
<summary><b>The agent's answer</b> (18 tool calls, 10 Evidence records cited)</summary>

> **Bottom line.** George Russell finished 42.9 seconds behind his Mercedes teammate Andrea Kimi Antonelli in Miami [E1]. The vast majority of this gap was built during the final stint of the race, alongside a steady loss of time after the early Safety Car restart [E1].
>
> ### Where the gap came from
>
> | Laps | What happened | Gap change | Main explanation | Support |
> | :--- | :--- | :--- | :--- | :--- |
> | 12-19 | Post-Safety Car green flag | RUS lost 5.3s | Held up in dirty air behind Piastri | **Consistent** |
> | 20-27 | Pit stop cycle | RUS lost 4.3s | Pit timing and out-lap relative performance | **Supported** |
> | 28-57 | Final hard tyre stint | RUS lost 32.7s | Unexplained pace difference on identical tyres | **Unanswerable** |
>
> ### Phase by phase
>
> #### Laps 12-19: Post-Safety Car green flag
> The gap grew by 5.3 seconds across eight clean racing laps, where Russell was slower than Antonelli on almost every lap [E3]. Russell spent five of these laps within 1.5 seconds of Oscar Piastri [E8]. **Consistent:** Russell's pace was hampered by dirty air while following Piastri closely [E8]. **Unanswerable:** The data cannot separate car performance from driver pace to explain why Russell was slower on identical tyres [E3, E11].
>
> #### Laps 20-27: Pit stop cycle
> Russell pitted on lap 20 and Antonelli on lap 26 [E1]. While Russell's actual time in the pit lane was slightly faster than Antonelli's [E6], he lost 4.3 seconds over the whole cycle [E1]. **Supported:** The timing of the stops meant Russell had to run his in-laps and out-laps at a disadvantage relative to Antonelli's cycle, costing him 2.1 seconds [E4, E6]. **Consistent:** After pitting, Russell dropped into traffic and spent several laps fighting back to clean air, costing him 2.2 seconds [E4, E7]. **Unanswerable:** Russell was also slower on clean laps by an average of 0.54 seconds per lap, which cannot be explained [E4].
>
> #### Laps 28-57: Final hard tyre stint
> Both drivers ran the hard tyre compound to the end, but Russell lost a massive 32.7 seconds [E1, E9]. **Unanswerable:** Antonelli was over a second per lap faster on average, which the data cannot explain, as it cannot measure car setup, tyre degradation, or driver performance [E2, E9]. **Consistent:** Russell lost roughly 5.0 seconds due to traffic, spending nine laps closely battling Max Verstappen, Charles Leclerc, and Piastri [E12].
>
> ### What the data can't tell you
> * Why Russell had a significant pace deficit to Antonelli on both tyre compounds [E3, E4, E9, E11].
> * The gap changes on laps 1 (where Russell lost 2.1s) and laps 2-5, which were not investigated [E1].
> * The effect of the Safety Car period on laps 6-11, where the field bunched up and Russell gained 1.6 seconds [E1].

</details>

<details>
<summary><b>The same model with no tools and no system prompt</b></summary>

> Based on the actual Formula 1 calendar and team lineups up to my knowledge cutoff (January 2025), **George Russell and Andrea Kimi Antonelli have never raced as teammates at the Miami Grand Prix.** 
>
> It is highly likely that this scenario is either from a video game (like *F1 24*), a simulated race, or a hypothetical future projection. 
>
> Here is the real-world context:
> * **The Teammate History:** From 2022 through the end of the 2024 season, George Russell’s Mercedes teammate was Lewis Hamilton. 
> * **Antonelli's Status:** Andrea Kimi Antonelli was announced as Russell's Mercedes teammate for the **2025 season**. 
> * **The Miami GP Timeline:** The Miami Grand Prix typically takes place in May. The first time Russell and Antonelli could realistically race as Mercedes teammates in Miami is May 2025.
>
> If this happened in a career mode of an F1 video game or a virtual simulation, a 43-second gap would usually be explained by a major incident—such as a front-wing damage pit stop, a severe penalty (like a drive-through or stop-go penalty), an mistimed pit stop under a Safety Car, or simply choosing the wrong tire strategy during changing weather conditions.

</details>

### Two more to try

- **A vague question:** "Why was Hamilton so slow in Brazil?" names one driver. The agent does not start researching: it looks up the race and asks who to compare him with, offering his teammate Leclerc, Hulkenberg who finished directly behind him, and Verstappen who started next to him.
- **A question the data cannot answer:** "In the Miami Grand Prix, was Russell slower than Antonelli because of a damaged floor, and did the team tell him to hold position?" The agent reports what the lap times show and says that damage and team orders are Unanswerable, because the dataset has no record of either.


## The page

- The header says what the agent explores, who it is for, how to use it and what it knows.
- The report appears in the conversation with a chart of the Gap, drawn by the page from the numbers in the response (no image model). The chart marks pit stops, Safety Car periods and the laps each Researcher investigated. Hover or use the arrow keys to read any lap; the lap-by-lap table is one click away.
- Every `[E#]` in the report is a button that opens the Evidence: source, race, laps and the data rows.
- The research log on the right updates live while the agents work.

## `/chat`

`POST /chat` keeps Project 1's shape. Tool calls made by sub-agents are in `tool_calls` too, each with an `agent` field.

```jsonc
// request
{ "message": "Why did Russell finish 43 seconds behind Antonelli in Miami?", "session_id": null }

// response
{
  "response": "**Bottom line.** Russell finished 42.9s behind Antonelli [E1]. ...",
  "session_id": "5f0c...",
  "tool_calls": [
    { "agent": "Lead",         "name": "gap_overview", "args": { "round": 4, "driver_a": "RUS", "driver_b": "ANT" }, "result": "{...}" },
    { "agent": "Researcher 1", "name": "lap_pace",     "args": { "driver_a": "RUS", "driver_b": "ANT", "lap_start": 28, "lap_end": 57 }, "result": "{...}" },
    { "agent": "Lead",         "name": "research",     "args": { "task": "...", "lap_start": 28, "lap_end": 57 }, "result": "Laps 28-57: ..." }
  ],
  "kind": "report",            // "reply" when the Lead asks a clarifying question
  "chart": { "points": [...], "phases": [...] },
  "evidence": { "E1": { "source": "OpenF1 laps", "rows": [...] } }
}
```

`GET /progress/{run_id}` streams the same activity as Server-Sent Events while a `/chat` request is running; the page uses it for the research log. `/chat` works without it.

## Run it locally

```bash
gcloud auth application-default login     # Gemini is called through Vertex AI
cp .env.example .env                      # set GOOGLE_CLOUD_PROJECT
uv run app.py                             # http://localhost:8000
uv run pytest                             # 46 tests, no network or model calls
```

The tests cover the arithmetic (the Gap, the Gap Phases, each analysis tool, Evidence records, the citation check) on small hand-built races whose expected values can be worked out by hand, and the `/chat` response shape with the research replaced by a fixed result.

## Layout

| Path | What it is |
|---|---|
| `app.py` | FastAPI app: `/`, `/chat`, `/progress/{run_id}`, `/clear`, `/races` |
| `research.py` | The Lead, the Researchers, the Writer, their prompts, the caps |
| `analysis.py` | The Gap, Gap Phases, the analysis tools, Evidence, the citation check |
| `f1data.py` | Loads the snapshot |
| `scripts/build_snapshot.py` | Builds the snapshot from OpenF1 and Jolpica |
| `data/` | The snapshot |
| `index.html` | The page |
| `tests/` | pytest suite |
| `CONTEXT.md`, `docs/adr/` | The project's vocabulary and the decision to ship a snapshot instead of calling the APIs live |

## Deployment

Cloud Run, continuous deployment from this repository's `main` branch with Google's buildpacks, behind IAP for `columbia.edu`. The service runs as a dedicated service account with `roles/aiplatform.user`, one instance at most (sessions are kept in memory), with `GOOGLE_CLOUD_PROJECT` as its only environment variable. No secrets are needed.
