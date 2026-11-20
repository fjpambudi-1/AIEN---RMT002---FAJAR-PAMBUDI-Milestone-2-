"""Read-only Neon Gold database client with execution limits and safe errors."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

import psycopg2
import psycopg2.extras

from src.sql_guard import SQLValidationError, validate_and_normalize_sql


class GoldQueryError(RuntimeError):
    """Safe error raised when querying Gold fails (never exposes credentials)."""


@dataclass(frozen=True)
class GoldQueryResult:
    """Structured result returned by a Gold query."""

    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    has_untrusted_text: bool


class GoldExecutor(Protocol):
    """Protocol for read-only Gold query execution."""

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> GoldQueryResult:
        ...


def _serialize_value(val: Any) -> Any:
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, str):
        # Truncate overly long text fields to keep context bounded
        return val[:500]
    return val


class NeonGoldClient:
    """Read-only PostgreSQL/Neon client for the gold schema."""

    def __init__(
        self,
        database_url: str,
        timeout_ms: int = 5000,
        max_rows: int = 200,
        max_bytes: int = 65536,
    ) -> None:
        self.database_url = database_url
        self.timeout_ms = timeout_ms
        self.max_rows = max_rows
        self.max_bytes = max_bytes

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> GoldQueryResult:
        """Validate SQL, run in a read-only transaction with timeout, and bound result size."""
        if not self.database_url:
            raise GoldQueryError("Konfigurasi koneksi Gold belum tersedia.")

        try:
            normalized_sql = validate_and_normalize_sql(sql, max_rows=self.max_rows)
        except SQLValidationError as exc:
            raise GoldQueryError(f"Kueri ditolak oleh validator: {exc}") from exc

        conn = None
        try:
            conn = psycopg2.connect(
                self.database_url,
                connect_timeout=5,
                options=f"-c default_transaction_read_only=on -c statement_timeout={self.timeout_ms}",
            )
            conn.set_session(readonly=True, autocommit=False)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SET LOCAL statement_timeout = %s;", (self.timeout_ms,))
                if params:
                    cur.execute(normalized_sql, params)
                else:
                    cur.execute(normalized_sql)

                raw_rows = cur.fetchmany(self.max_rows + 1)
                truncated = len(raw_rows) > self.max_rows
                if truncated:
                    raw_rows = raw_rows[: self.max_rows]

                columns = [desc[0] for desc in (cur.description or [])]
                rows: list[dict[str, Any]] = []
                total_bytes = 2
                has_untrusted = "exception_note" in columns

                for r in raw_rows:
                    clean_row = {k: _serialize_value(v) for k, v in dict(r).items()}
                    encoded_len = len(json.dumps(clean_row, ensure_ascii=False).encode("utf-8")) + 2
                    if total_bytes + encoded_len > self.max_bytes:
                        truncated = True
                        break
                    total_bytes += encoded_len
                    rows.append(clean_row)

            conn.rollback()
            return GoldQueryResult(
                sql=normalized_sql,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
                has_untrusted_text=has_untrusted,
            )
        except GoldQueryError:
            raise
        except psycopg2.errors.QueryCanceled as exc:
            raise GoldQueryError("Kueri melebihi batas waktu eksekusi.") from exc
        except psycopg2.errors.InsufficientPrivilege as exc:
            raise GoldQueryError("Operasi ditolak oleh izin database hanya-baca.") from exc
        except psycopg2.Error as exc:
            raise GoldQueryError("Gagal menjalankan kueri pada database Gold.") from exc
        finally:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    conn.close()
                except Exception:
                    pass
