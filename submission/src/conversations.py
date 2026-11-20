"""SQLite conversation store with strict per-user ownership enforcement."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ConversationStore:
    """Manages conversations and messages in local SQLite."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
                ON conversations (owner_id, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    data_basis TEXT NOT NULL DEFAULT '',
                    outcome TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conv_id
                ON messages (conversation_id, id ASC)
                """
            )

    def create_conversation(self, owner_id: str, title: str = "Percakapan Baru") -> dict[str, str]:
        """Create a new conversation owned by owner_id."""
        conv_id = str(uuid.uuid4())
        now = utc_now_iso()
        clean_title = (title or "Percakapan Baru").strip()[:80] or "Percakapan Baru"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, owner_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conv_id, owner_id, clean_title, now, now),
            )
        return {"id": conv_id, "title": clean_title, "updated_at": now}

    def list_conversations(self, owner_id: str) -> list[dict[str, str]]:
        """List conversations belonging strictly to owner_id."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, title, updated_at
                FROM conversations
                WHERE owner_id = ?
                ORDER BY updated_at DESC, rowid DESC
                """,
                (owner_id,),
            ).fetchall()
        return [{"id": r["id"], "title": r["title"], "updated_at": r["updated_at"]} for r in rows]

    def get_conversation(self, conversation_id: str, owner_id: str) -> dict | None:
        """Return conversation and its messages only if owned by owner_id."""
        with self._connect() as conn:
            conv = conn.execute(
                """
                SELECT id, title, updated_at
                FROM conversations
                WHERE id = ? AND owner_id = ?
                """,
                (conversation_id, owner_id),
            ).fetchone()
            if conv is None:
                return None

            msgs = conn.execute(
                """
                SELECT role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()

        return {
            "id": conv["id"],
            "title": conv["title"],
            "updated_at": conv["updated_at"],
            "messages": [
                {"role": m["role"], "content": m["content"], "created_at": m["created_at"]}
                for m in msgs
            ],
        }

    def is_owner(self, conversation_id: str, owner_id: str) -> bool:
        """Check whether conversation_id exists and belongs to owner_id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
        return row is not None

    def add_message(
        self,
        conversation_id: str,
        owner_id: str,
        role: str,
        content: str,
        data_basis: str = "",
        outcome: str = "",
    ) -> bool:
        """Append a message if conversation_id is owned by owner_id."""
        now = utc_now_iso()
        with self._connect() as conn:
            conv = conn.execute(
                "SELECT title FROM conversations WHERE id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
            if conv is None:
                return False

            conn.execute(
                """
                INSERT INTO messages (conversation_id, role, content, data_basis, outcome, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, data_basis, outcome, now),
            )

            new_title = conv["title"]
            if role == "user" and conv["title"] == "Percakapan Baru":
                snippet = " ".join(content.strip().split())
                if len(snippet) > 55:
                    snippet = snippet[:52] + "..."
                if snippet:
                    new_title = snippet

            conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (new_title, now, conversation_id, owner_id),
            )
        return True

    def get_recent_messages(
        self,
        conversation_id: str,
        owner_id: str,
        limit: int = 10,
    ) -> list[dict[str, str]]:
        """Return the most recent bounded history for an owned conversation."""
        if limit <= 0:
            return []
        with self._connect() as conn:
            conv = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
            if conv is None:
                return []

            rows = conn.execute(
                """
                SELECT role, content, data_basis, outcome, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()

        return [
            {
                "role": r["role"],
                "content": r["content"],
                "data_basis": r["data_basis"],
                "outcome": r["outcome"],
                "created_at": r["created_at"],
            }
            for r in reversed(rows)
        ]
