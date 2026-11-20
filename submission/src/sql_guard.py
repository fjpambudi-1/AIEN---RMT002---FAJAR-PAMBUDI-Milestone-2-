"""Strict read-only SQL validator and normalizer for Gold analytics queries."""

from __future__ import annotations

import re
import sqlparse
from sqlparse.sql import Identifier, IdentifierList, Token
from sqlparse.tokens import Comment, DML, Keyword, Name, Punctuation, String, Whitespace


class SQLValidationError(ValueError):
    """Raised when a SQL query violates read-only or allowlist rules."""


ALLOWED_TABLES: dict[str, set[str]] = {
    "gold.shipment_fact": {
        "shipment_id",
        "order_date",
        "carrier_id",
        "origin_hub",
        "destination_city",
        "service_level",
        "promised_delivery_at_utc",
        "delivered_at_utc",
        "delivery_status",
        "shipping_fee_idr",
        "refund_amount_idr",
        "exception_note",
    },
    "gold.carrier_daily": {
        "metric_date",
        "carrier_id",
        "shipment_count",
        "delivered_count",
        "on_time_delivered_count",
        "late_delivered_count",
        "in_transit_count",
        "shipping_fee_idr",
        "refund_amount_idr",
    },
    "gold.city_daily": {
        "metric_date",
        "destination_city",
        "shipment_count",
        "delivered_count",
        "on_time_delivered_count",
        "late_delivered_count",
        "in_transit_count",
        "shipping_fee_idr",
        "refund_amount_idr",
    },
}

FORBIDDEN_KEYWORDS: set[str] = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "REPLACE",
    "MERGE",
    "UPSERT",
    "GRANT",
    "REVOKE",
    "COPY",
    "CALL",
    "EXEC",
    "EXECUTE",
    "DO",
    "SET",
    "RESET",
    "SHOW",
    "EXPLAIN",
    "ANALYZE",
    "VACUUM",
    "LOCK",
    "LISTEN",
    "UNLISTEN",
    "NOTIFY",
    "PREPARE",
    "DEALLOCATE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "INTO",
    "RETURNING",
    "SHARE",
    "ATTACH",
    "DETACH",
    "PRAGMA",
}

FORBIDDEN_IDENTIFIERS: set[str] = {
    "pg_catalog",
    "information_schema",
    "pg_toast",
    "pg_temp",
    "sqlite_master",
    "sqlite_temp_master",
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "lo_get",
    "lo_put",
    "dblink",
    "dblink_exec",
    "dblink_connect",
    "query_to_xml",
    "current_setting",
    "set_config",
    "current_user",
    "session_user",
    "inet_server_addr",
    "inet_client_addr",
    "txid_current",
}

ALLOWED_SQL_FUNCTIONS: set[str] = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "coalesce",
    "nullif",
    "round",
    "abs",
    "ceil",
    "ceiling",
    "floor",
    "cast",
    "date_trunc",
    "date_part",
    "extract",
    "to_char",
    "to_date",
    "lower",
    "upper",
    "trim",
    "length",
    "substring",
    "left",
    "right",
    "row_number",
    "rank",
    "dense_rank",
    "lag",
    "lead",
    "greatest",
    "least",
    "strftime",
    "date",
    "julianday",
}

# Keywords / units that can appear as bare words inside expressions like DATE_TRUNC('week', ...) or EXTRACT(DOW FROM ...)
SQL_EXPR_WORDS: set[str] = {
    "year",
    "month",
    "week",
    "day",
    "hour",
    "minute",
    "second",
    "dow",
    "isodow",
    "epoch",
    "numeric",
    "decimal",
    "integer",
    "int",
    "bigint",
    "float",
    "double",
    "precision",
    "text",
    "varchar",
    "date",
    "timestamp",
    "timestamptz",
    "boolean",
    "bool",
    "interval",
    "asc",
    "desc",
    "nulls",
    "first",
    "last",
    "true",
    "false",
    "null",
}


def _strip_quotes(name: str) -> str:
    name = name.strip()
    if (name.startswith('"') and name.endswith('"')) or (
        name.startswith("`") and name.endswith("`")
    ):
        return name[1:-1]
    return name


def _iter_non_ws_tokens(tokens: list[Token]) -> list[Token]:
    result: list[Token] = []
    for tok in tokens:
        if tok.is_whitespace:
            continue
        if tok.is_group:
            result.extend(_iter_non_ws_tokens(tok.tokens))
        else:
            result.append(tok)
    return result


def validate_and_normalize_sql(sql: str, max_rows: int = 200) -> str:
    """Validate that SQL is a single read-only SELECT against allowed Gold tables/columns.

    Returns normalized SQL with an enforced LIMIT <= max_rows.
    Raises SQLValidationError on any violation.
    """
    if not sql or not isinstance(sql, str):
        raise SQLValidationError("Kueri SQL tidak boleh kosong.")

    cleaned = sql.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()

    if not cleaned:
        raise SQLValidationError("Kueri SQL tidak boleh kosong.")

    # Reject any remaining semicolons outside string literals by using sqlparse.split
    statements = [s.strip() for s in sqlparse.split(cleaned) if s.strip()]
    if len(statements) != 1:
        raise SQLValidationError("Hanya satu pernyataan SELECT yang diizinkan.")

    parsed_list = sqlparse.parse(statements[0])
    if len(parsed_list) != 1:
        raise SQLValidationError("Kueri SQL tidak valid.")

    stmt = parsed_list[0]
    flat_tokens = _iter_non_ws_tokens(stmt.tokens)
    if not flat_tokens:
        raise SQLValidationError("Kueri SQL tidak boleh kosong.")

    # Reject comments anywhere
    for tok in flat_tokens:
        if tok.ttype in Comment or tok.ttype is Comment.Single or tok.ttype is Comment.Multiline:
            raise SQLValidationError("Komentar SQL tidak diizinkan.")
        if tok.ttype in (Punctuation,) and tok.value == ";":
            raise SQLValidationError("Multi-statement SQL tidak diizinkan.")

    # Must start with SELECT or WITH
    first_upper = flat_tokens[0].value.upper()
    if first_upper not in {"SELECT", "WITH"}:
        raise SQLValidationError("Hanya pernyataan SELECT yang diizinkan.")

    if first_upper == "SELECT" and stmt.get_type() != "SELECT":
        raise SQLValidationError("Hanya pernyataan SELECT yang diizinkan.")

    # Check forbidden keywords and forbidden identifiers across all non-string tokens
    has_select = False
    for tok in flat_tokens:
        if tok.ttype in String or tok.ttype is String.Single or tok.ttype is String.Symbol:
            continue
        val_upper = tok.value.upper()
        val_lower = tok.value.lower()
        if val_upper == "SELECT":
            has_select = True
        if val_upper in FORBIDDEN_KEYWORDS:
            raise SQLValidationError(f"Kata kunci '{val_upper}' tidak diizinkan.")
        if val_lower in FORBIDDEN_IDENTIFIERS:
            raise SQLValidationError(f"Objek atau fungsi '{tok.value}' tidak diizinkan.")

    if not has_select:
        raise SQLValidationError("Pernyataan harus mengandung SELECT.")

    # Extract CTE names if WITH is used
    cte_names: set[str] = set()
    if first_upper == "WITH":
        # Match CTE names: WITH [RECURSIVE] name AS ( ... ), name2 AS ( ... )
        for idx, tok in enumerate(flat_tokens):
            if tok.value.upper() == "AS" and idx > 0:
                prev = flat_tokens[idx - 1]
                if prev.ttype in Name or prev.ttype is None:
                    # Ensure it's a CTE definition (preceded by WITH or comma)
                    if idx >= 2 and flat_tokens[idx - 2].value.upper() in {"WITH", "RECURSIVE", ","}:
                        cte_names.add(_strip_quotes(prev.value).lower())

    # Validate table references after FROM / JOIN
    referenced_tables: set[str] = set()
    aliases: set[str] = set()
    i = 0
    while i < len(flat_tokens):
        tok = flat_tokens[i]
        if tok.ttype in Keyword and tok.value.upper() in {"FROM", "JOIN"}:
            if i + 1 >= len(flat_tokens):
                raise SQLValidationError("Klausa FROM/JOIN tidak lengkap.")
            nxt = flat_tokens[i + 1]
            # Subquery: FROM (SELECT ...)
            if nxt.value == "(":
                i += 1
                continue

            # Expect schema.table or CTE name
            # In flat_tokens, schema.table is usually 3 tokens: schema, '.', table
            if (
                i + 3 < len(flat_tokens)
                and flat_tokens[i + 2].value == "."
                and nxt.ttype not in String
            ):
                schema_name = _strip_quotes(nxt.value).lower()
                table_name = _strip_quotes(flat_tokens[i + 3].value).lower()
                full_name = f"{schema_name}.{table_name}"
                if full_name not in ALLOWED_TABLES:
                    raise SQLValidationError(
                        f"Tabel '{full_name}' tidak diizinkan. Hanya tabel gold yang diizinkan."
                    )
                referenced_tables.add(full_name)
                # Check optional alias: AS alias or bare alias
                pos = i + 4
                if pos < len(flat_tokens):
                    if flat_tokens[pos].value.upper() == "AS" and pos + 1 < len(flat_tokens):
                        aliases.add(_strip_quotes(flat_tokens[pos + 1].value).lower())
                    elif (
                        flat_tokens[pos].ttype in Name
                        and flat_tokens[pos].value.upper()
                        not in {"WHERE", "GROUP", "ORDER", "HAVING", "LIMIT", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "ON", "UNION", "EXCEPT", "INTERSECT"}
                    ):
                        aliases.add(_strip_quotes(flat_tokens[pos].value).lower())
                i += 4
                continue
            else:
                # Single identifier after FROM/JOIN: only allowed if it's a defined CTE
                ident = _strip_quotes(nxt.value).lower()
                if ident not in cte_names:
                    raise SQLValidationError(
                        f"Tabel '{nxt.value}' tidak diizinkan; gunakan nama lengkap schema gold (misal gold.shipment_fact)."
                    )
                pos = i + 2
                if pos < len(flat_tokens) and flat_tokens[pos].value.upper() == "AS" and pos + 1 < len(flat_tokens):
                    aliases.add(_strip_quotes(flat_tokens[pos + 1].value).lower())
                i += 2
                continue
        i += 1

    if not referenced_tables:
        raise SQLValidationError("Kueri harus membaca minimal satu tabel gold yang diizinkan.")

    if "gold.carrier_daily" in referenced_tables and "gold.city_daily" in referenced_tables:
        raise SQLValidationError(
            "Tidak boleh menggabungkan gold.carrier_daily dan gold.city_daily dalam satu kueri."
        )

    # Collect allowed columns from referenced tables + CTE/SELECT aliases
    allowed_cols: set[str] = set()
    for tbl in referenced_tables:
        allowed_cols.update(ALLOWED_TABLES[tbl])

    # Collect AS <alias> names so ORDER BY / GROUP BY / outer SELECT can reference them
    select_aliases: set[str] = set()
    for idx, tok in enumerate(flat_tokens):
        if tok.ttype in Keyword and tok.value.upper() == "AS" and idx + 1 < len(flat_tokens):
            nxt = flat_tokens[idx + 1]
            if nxt.value.upper() not in SQL_EXPR_WORDS:
                select_aliases.add(_strip_quotes(nxt.value).lower())

    # Validate qualified column references (e.g. sf.order_date or gold.shipment_fact.order_date)
    # and bare Name tokens that are not functions, aliases, CTEs, or SQL expression words
    for idx, tok in enumerate(flat_tokens):
        if tok.ttype in String or tok.ttype is String.Single or tok.ttype is String.Symbol:
            continue
        if tok.ttype in Name or tok.ttype is Name:
            val = _strip_quotes(tok.value).lower()
            if val in FORBIDDEN_IDENTIFIERS:
                raise SQLValidationError(f"Identifier '{tok.value}' tidak diizinkan.")

            # If followed by '(', it's a function call
            is_func = idx + 1 < len(flat_tokens) and flat_tokens[idx + 1].value == "("
            if is_func:
                if val not in ALLOWED_SQL_FUNCTIONS and val not in cte_names:
                    raise SQLValidationError(f"Fungsi SQL '{tok.value}' tidak diizinkan.")
                continue

            # If preceded by '.', it's either table name (already checked) or column name
            if idx >= 2 and flat_tokens[idx - 1].value == ".":
                prev_word = _strip_quotes(flat_tokens[idx - 2].value).lower()
                if prev_word == "gold":
                    # gold.table_name
                    continue
                if prev_word in aliases or prev_word in cte_names or f"gold.{prev_word}" in referenced_tables:
                    if val not in allowed_cols and val not in select_aliases:
                        raise SQLValidationError(f"Kolom '{tok.value}' tidak ada pada tabel yang diizinkan.")
                continue

            # If followed by '.', it's a schema/table/alias prefix
            if idx + 1 < len(flat_tokens) and flat_tokens[idx + 1].value == ".":
                if val not in {"gold"} and val not in aliases and val not in cte_names and f"gold.{val}" not in referenced_tables:
                    raise SQLValidationError(f"Referensi '{tok.value}' tidak diizinkan.")
                continue

            # Otherwise bare identifier: must be allowed column, alias, CTE, or SQL expr word
            if (
                val not in allowed_cols
                and val not in aliases
                and val not in cte_names
                and val not in select_aliases
                and val not in SQL_EXPR_WORDS
            ):
                raise SQLValidationError(f"Kolom atau identifier '{tok.value}' tidak diizinkan.")

    # Enforce LIMIT <= max_rows
    limit_match = re.search(r"\bLIMIT\s+(\d+)\s*$", statements[0], flags=re.IGNORECASE)
    if limit_match:
        requested_limit = int(limit_match.group(1))
        if requested_limit <= 0:
            raise SQLValidationError("LIMIT harus lebih besar dari 0.")
        if requested_limit > max_rows:
            normalized = re.sub(
                r"\bLIMIT\s+\d+\s*$",
                f"LIMIT {max_rows}",
                statements[0],
                flags=re.IGNORECASE,
            )
            return normalized
        return statements[0]

    return f"{statements[0]} LIMIT {max_rows}"
