"""Tests for bearer token auth, per-user conversation ownership (IDOR), and SQLite persistence."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import SyncASGIClient as TestClient

from src.api import app, get_agent, get_store
from src.config import Settings, get_settings
from src.conversations import ConversationStore


def _test_settings(sqlite_path: str) -> Settings:
    return Settings(
        gold_database_url="",
        demo_token_a="test-token-a",
        demo_token_b="test-token-b",
        sqlite_path=sqlite_path,
        openai_api_key="test-key",
        openai_base_url=None,
        llm_model="test-model",
        sql_timeout_ms=5000,
        sql_max_rows=200,
        sql_max_bytes=65536,
        max_history_messages=10,
        max_message_length=2000,
        agent_max_steps=4,
    )


def test_healthz_no_auth_required(tmp_path: Path):
    db_file = str(tmp_path / "conv.sqlite3")
    settings = _test_settings(db_file)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: ConversationStore(db_file)
    try:
        client = TestClient(app)
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
    finally:
        app.dependency_overrides.clear()


def test_invalid_and_missing_tokens_return_401(tmp_path: Path):
    db_file = str(tmp_path / "conv.sqlite3")
    settings = _test_settings(db_file)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: ConversationStore(db_file)
    try:
        client = TestClient(app)
        assert client.get("/api/v1/conversations").status_code == 401
        assert (
            client.get(
                "/api/v1/conversations",
                headers={"Authorization": "Bearer wrong-token"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/conversations",
                headers={"Authorization": "Basic abc"},
                json={},
            ).status_code
            == 401
        )
    finally:
        app.dependency_overrides.clear()


def test_cross_user_isolation_and_persistence(tmp_path: Path):
    db_file = str(tmp_path / "conv.sqlite3")
    settings = _test_settings(db_file)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_store] = lambda: ConversationStore(db_file)

    class StubAgent:
        def run(self, user_message: str, history: list[dict[str, str]]):
            from src.agent import AgentResult

            return AgentResult(
                answer="Total kiriman Agustus 2026 adalah 217 kiriman.",
                data_basis="gold.carrier_daily (2026-08-01 s/d 2026-08-31)",
                outcome="answered",
            )

    app.dependency_overrides[get_agent] = lambda: StubAgent()

    try:
        client = TestClient(app)
        headers_a = {"Authorization": "Bearer test-token-a"}
        headers_b = {"Authorization": "Bearer test-token-b"}

        # User A creates conversation (even if client passes an extra user_id, server ignores it)
        created = client.post(
            "/api/v1/conversations",
            headers=headers_a,
            json={"user_id": "user_b"},
        )
        assert created.status_code == 201
        conv_id = created.json()["id"]

        # User A sends a message
        msg_resp = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers_a,
            json={"content": "Berapa jumlah kiriman pada Agustus 2026?"},
        )
        assert msg_resp.status_code == 200
        assert msg_resp.json()["outcome"] == "answered"

        # User B lists conversations -> must NOT see A's conversation
        list_b = client.get("/api/v1/conversations", headers=headers_b)
        assert list_b.status_code == 200
        assert list_b.json()["conversations"] == []

        # O1: User B tries to read A's conversation -> 404
        read_b = client.get(f"/api/v1/conversations/{conv_id}", headers=headers_b)
        assert read_b.status_code == 404

        # O2: User B tries to send message to A's conversation -> 404
        send_b = client.post(
            f"/api/v1/conversations/{conv_id}/messages",
            headers=headers_b,
            json={"content": "Halo"},
        )
        assert send_b.status_code == 404

        # Verify A's message count is unchanged (still 2: 1 user + 1 assistant)
        read_a = client.get(f"/api/v1/conversations/{conv_id}", headers=headers_a)
        assert read_a.status_code == 200
        assert len(read_a.json()["messages"]) == 2

        # Simulate API restart by creating a new ConversationStore pointing to the same SQLite file
        restarted_store = ConversationStore(db_file)
        persisted = restarted_store.get_conversation(conv_id, "user_a")
        assert persisted is not None
        assert len(persisted["messages"]) == 2
        assert restarted_store.get_conversation(conv_id, "user_b") is None
    finally:
        app.dependency_overrides.clear()
