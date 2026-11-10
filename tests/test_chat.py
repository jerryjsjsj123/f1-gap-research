"""POST /chat keeps Project 1's response shape. The Research itself is replaced by a fixed result."""

import pytest
from fastapi.testclient import TestClient

import app as web
import research


@pytest.fixture
def client(monkeypatch):
    async def fixed_research(session, message, emit):
        emit({"type": "status", "agent": "Lead", "text": "thinking"})
        return {
            "response": "VER lost 3.1s to NOR in the pit stops [E2].",
            "tool_calls": [
                {"agent": "Lead", "name": "gap_overview", "args": {"round": 20}, "result": "{}"},
                {"agent": "Researcher 1", "name": "pit_stops", "args": {"lap_start": 16, "lap_end": 18}, "result": "{}"},
            ],
            "kind": "report",
            "chart": {"a": "VER", "b": "NOR", "points": []},
            "evidence": {"E2": {"id": "E2", "source": "OpenF1 pit + laps"}},
        }

    monkeypatch.setattr(research, "run_research", fixed_research)
    web.sessions.clear()
    return TestClient(web.app)


def test_chat_returns_the_response_the_session_and_every_tool_call(client):
    body = client.post("/chat", json={"message": "Why did VER finish behind NOR in Brazil?"}).json()

    assert body["response"] == "VER lost 3.1s to NOR in the pit stops [E2]."
    assert isinstance(body["session_id"], str) and body["session_id"]
    assert [call["name"] for call in body["tool_calls"]] == ["gap_overview", "pit_stops"]


def test_every_tool_call_names_the_agent_that_made_it_including_sub_agents(client):
    body = client.post("/chat", json={"message": "Why did VER finish behind NOR in Brazil?"}).json()

    assert [call["agent"] for call in body["tool_calls"]] == ["Lead", "Researcher 1"]
    assert all({"agent", "name", "args", "result"} <= set(call) for call in body["tool_calls"])


def test_a_session_id_continues_the_same_conversation(client):
    first = client.post("/chat", json={"message": "first"}).json()
    second = client.post("/chat", json={"message": "second", "session_id": first["session_id"]}).json()

    assert second["session_id"] == first["session_id"]
    assert len(web.sessions) == 1


def test_a_failure_still_answers_in_the_chat_shape(client, monkeypatch):
    async def broken(session, message, emit):
        raise RuntimeError("quota exceeded")

    monkeypatch.setattr(research, "run_research", broken)

    response = client.post("/chat", json={"message": "anything"})

    assert response.status_code == 200
    assert "quota exceeded" in response.json()["response"]
    assert response.json()["tool_calls"] == []


def test_an_empty_message_is_rejected(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
