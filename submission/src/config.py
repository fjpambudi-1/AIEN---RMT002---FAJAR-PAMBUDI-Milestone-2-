"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    """Application settings sourced exclusively from environment variables."""

    gold_database_url: str
    demo_token_a: str
    demo_token_b: str
    sqlite_path: str
    openai_api_key: str
    openai_base_url: str | None
    llm_model: str
    sql_timeout_ms: int
    sql_max_rows: int
    sql_max_bytes: int
    max_history_messages: int
    max_message_length: int
    agent_max_steps: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached runtime settings."""
    return Settings(
        gold_database_url=os.getenv("GOLD_DATABASE_URL", "").strip(),
        demo_token_a=os.getenv("DEMO_TOKEN_A", "").strip(),
        demo_token_b=os.getenv("DEMO_TOKEN_B", "").strip(),
        sqlite_path=os.getenv("SQLITE_PATH", "./data/conversations.sqlite3").strip(),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini").strip(),
        sql_timeout_ms=int(os.getenv("SQL_TIMEOUT_MS", "5000")),
        sql_max_rows=int(os.getenv("SQL_MAX_ROWS", "200")),
        sql_max_bytes=int(os.getenv("SQL_MAX_BYTES", "65536")),
        max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "10")),
        max_message_length=int(os.getenv("MAX_MESSAGE_LENGTH", "2000")),
        agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "4")),
    )
