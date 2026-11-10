"""F1 Gap Research: a web app that explains where the Gap between two F1 drivers came from.

POST /chat keeps Project 1's response shape (response, session_id, tool_calls), and every tool call
carries the agent that made it, Researchers included. GET /progress/{run_id} streams what the agents
are doing while a /chat request is still running.
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

HERE = Path(__file__).parent
# Locally, GOOGLE_CLOUD_PROJECT comes from .env. On Cloud Run the service sets it.
load_dotenv(HERE / ".env")

import research  # noqa: E402  (reads the environment when imported)
from f1data import list_races  # noqa: E402

# session_id -> Session. In-memory, single process: Cloud Run runs one instance of this service.
sessions: dict[str, research.Session] = {}
# run_id -> the progress events of a /chat request in flight, for the page to stream.
progress: dict[str, asyncio.Queue] = {}

app = FastAPI(title="F1 Gap Research")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    # Optional: the page picks an id and listens on /progress/{run_id} before it posts.
    run_id: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    """Project 1's shape, plus what the page needs to draw the report."""

    response: str
    session_id: str
    # Every tool call, in order: {"agent", "name", "args", "result"}. Researchers' calls are included.
    tool_calls: list[dict[str, Any]]
    kind: str = "reply"  # "report" after a Research, "reply" for a clarifying question
    chart: dict[str, Any] | None = None
    evidence: dict[str, Any] = {}


def _queue(run_id: str) -> asyncio.Queue:
    return progress.setdefault(run_id, asyncio.Queue())


@app.get("/")
def index() -> FileResponse:
    # no-cache: browsers otherwise keep serving an old page after a redeploy
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/races")
def races() -> list[dict]:
    """The races in the snapshot, for the page's introduction."""
    return [{"round": race["round"], "name": race["name"], "date": race["date"]} for race in list_races()]


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Run one conversation turn, creating the session on first use."""
    session_id = request.session_id or str(uuid.uuid4())
    session = sessions.setdefault(session_id, research.Session())
    queue = _queue(request.run_id) if request.run_id else None
    emit = queue.put_nowait if queue else (lambda event: None)
    try:
        # Concurrent requests for one session must not interleave their turns.
        async with session.lock:
            result = await research.run_research(session, request.message, emit)
    except Exception as e:
        # Auth, quota, a model that won't stop: answer in the usual shape, not with a dropped request.
        if "RateLimit" in type(e).__name__ or "429" in str(e):
            text = "The Gemini service is busy right now and turned the request away. Please ask again in a minute."
        else:
            text = f"Something went wrong while researching this ({type(e).__name__}: {str(e)[:300]}). Please try again."
        result = {"response": text, "tool_calls": [], "kind": "error"}
    finally:
        emit({"type": "done"})
    return ChatResponse(session_id=session_id, **result)


@app.get("/progress/{run_id}")
async def stream_progress(run_id: str) -> StreamingResponse:
    """Server-Sent Events: what the Lead, the Researchers and the Writer are doing right now."""
    queue = _queue(run_id)

    async def events():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # proxies drop a stream that stays silent
                    continue
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] == "done":
                    break
        finally:
            progress.pop(run_id, None)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/clear")
def clear(session_id: str) -> dict[str, str]:
    """Forget a session's conversation and Evidence."""
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
