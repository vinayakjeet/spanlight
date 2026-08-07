from __future__ import annotations

from fastapi.testclient import TestClient


def test_agent_run_returns_a_reply(client: TestClient) -> None:
    response = client.post("/agent/run", json={"prompt": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "mock reply: hello"
    assert body["provider"] == "mock"
    assert body["model"] == "mock-echo"
    # Returned so a visitor can find their own trace rather than take it on faith.
    assert body["session_id"]


def test_each_request_gets_its_own_session(client: TestClient) -> None:
    first = client.post("/agent/run", json={"prompt": "hello"}).json()["session_id"]
    second = client.post("/agent/run", json={"prompt": "hello"}).json()["session_id"]

    assert first != second


def test_agent_run_rejects_a_missing_prompt(client: TestClient) -> None:
    assert client.post("/agent/run", json={}).status_code == 422
