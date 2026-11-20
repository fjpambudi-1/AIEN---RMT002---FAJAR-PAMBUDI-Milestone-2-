"""Tests for strict read-only SQL validation in src/sql_guard.py."""

import pytest

from src.sql_guard import SQLValidationError, validate_and_normalize_sql


def test_valid_select_appends_limit():
    sql = "SELECT COUNT(*) AS total FROM gold.shipment_fact WHERE order_date >= '2026-08-01'"
    out = validate_and_normalize_sql(sql, max_rows=200)
    assert out.endswith("LIMIT 200")


def test_valid_select_clamps_excessive_limit():
    sql = "SELECT carrier_id, SUM(shipment_count) AS total FROM gold.carrier_daily GROUP BY carrier_id LIMIT 5000"
    out = validate_and_normalize_sql(sql, max_rows=200)
    assert out.endswith("LIMIT 200")


def test_valid_cte_and_rate_query():
    sql = (
        "WITH agg AS ("
        "  SELECT carrier_id, SUM(on_time_delivered_count) AS ot, SUM(delivered_count) AS deliv "
        "  FROM gold.carrier_daily "
        "  WHERE metric_date BETWEEN '2026-08-01' AND '2026-08-31' "
        "  GROUP BY carrier_id"
        ") "
        "SELECT carrier_id, ot, deliv, ROUND(ot * 100.0 / NULLIF(deliv, 0), 2) AS on_time_pct "
        "FROM agg ORDER BY on_time_pct DESC"
    )
    out = validate_and_normalize_sql(sql, max_rows=200)
    assert "LIMIT 200" in out


@pytest.mark.parametrize(
    "bad_sql",
    [
        "UPDATE gold.shipment_fact SET delivery_status = 'delivered'",
        "DELETE FROM gold.shipment_fact WHERE shipment_id = 'SHP-00001'",
        "INSERT INTO gold.shipment_fact (shipment_id) VALUES ('X')",
        "DROP TABLE gold.shipment_fact",
        "ALTER TABLE gold.shipment_fact ADD COLUMN x text",
        "COPY gold.shipment_fact TO STDOUT",
        "SELECT * FROM gold.shipment_fact; DROP TABLE gold.shipment_fact",
        "SELECT * FROM gold.shipment_fact -- comment",
        "SELECT * FROM gold.shipment_fact /* block comment */",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
        "SELECT * FROM public.users",
        "SELECT * FROM shipment_fact",
        "SELECT pg_sleep(5) FROM gold.shipment_fact",
        "SELECT * INTO temp_tbl FROM gold.shipment_fact",
        "SELECT * FROM gold.carrier_daily JOIN gold.city_daily ON carrier_daily.metric_date = city_daily.metric_date",
        "SELECT secret_column FROM gold.shipment_fact",
    ],
)
def test_rejects_unsafe_or_disallowed_sql(bad_sql: str):
    with pytest.raises(SQLValidationError):
        validate_and_normalize_sql(bad_sql, max_rows=200)
