-- Gold schema initialization following gold-contract.md
-- All timestamps are UTC; all monetary values are in synthetic IDR.
-- Dataset coverage: 2026-07-01 through 2026-09-20 (order_date / metric_date).

SET TIME ZONE 'UTC';

CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS gold.shipment_fact (
    shipment_id text PRIMARY KEY,
    order_date date NOT NULL CHECK (order_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-20'),
    carrier_id text NOT NULL CHECK (carrier_id IN ('C01', 'C02', 'C03')),
    origin_hub text NOT NULL,
    destination_city text NOT NULL,
    service_level text NOT NULL CHECK (service_level IN ('standard', 'express')),
    promised_delivery_at_utc timestamptz NOT NULL,
    delivered_at_utc timestamptz NULL,
    delivery_status text NOT NULL CHECK (delivery_status IN ('delivered', 'in_transit')),
    shipping_fee_idr numeric(12,2) NOT NULL CHECK (shipping_fee_idr >= 0),
    refund_amount_idr numeric(12,2) NOT NULL CHECK (refund_amount_idr >= 0),
    exception_note text NULL,
    CONSTRAINT chk_delivered_timestamp_consistency CHECK (
        (delivery_status = 'delivered' AND delivered_at_utc IS NOT NULL)
        OR (delivery_status = 'in_transit' AND delivered_at_utc IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS gold.carrier_daily (
    metric_date date NOT NULL CHECK (metric_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-20'),
    carrier_id text NOT NULL CHECK (carrier_id IN ('C01', 'C02', 'C03')),
    shipment_count integer NOT NULL CHECK (shipment_count >= 0),
    delivered_count integer NOT NULL CHECK (delivered_count >= 0),
    on_time_delivered_count integer NOT NULL CHECK (on_time_delivered_count >= 0),
    late_delivered_count integer NOT NULL CHECK (late_delivered_count >= 0),
    in_transit_count integer NOT NULL CHECK (in_transit_count >= 0),
    shipping_fee_idr numeric(12,2) NOT NULL CHECK (shipping_fee_idr >= 0),
    refund_amount_idr numeric(12,2) NOT NULL CHECK (refund_amount_idr >= 0),
    PRIMARY KEY (metric_date, carrier_id),
    CONSTRAINT chk_carrier_counts_consistent CHECK (
        delivered_count = on_time_delivered_count + late_delivered_count
        AND shipment_count = delivered_count + in_transit_count
    )
);

CREATE TABLE IF NOT EXISTS gold.city_daily (
    metric_date date NOT NULL CHECK (metric_date BETWEEN DATE '2026-07-01' AND DATE '2026-09-20'),
    destination_city text NOT NULL,
    shipment_count integer NOT NULL CHECK (shipment_count >= 0),
    delivered_count integer NOT NULL CHECK (delivered_count >= 0),
    on_time_delivered_count integer NOT NULL CHECK (on_time_delivered_count >= 0),
    late_delivered_count integer NOT NULL CHECK (late_delivered_count >= 0),
    in_transit_count integer NOT NULL CHECK (in_transit_count >= 0),
    shipping_fee_idr numeric(12,2) NOT NULL CHECK (shipping_fee_idr >= 0),
    refund_amount_idr numeric(12,2) NOT NULL CHECK (refund_amount_idr >= 0),
    PRIMARY KEY (metric_date, destination_city),
    CONSTRAINT chk_city_counts_consistent CHECK (
        delivered_count = on_time_delivered_count + late_delivered_count
        AND shipment_count = delivered_count + in_transit_count
    )
);

-- Seed synthetic Gold data from mounted CSVs
TRUNCATE TABLE gold.shipment_fact, gold.carrier_daily, gold.city_daily;

COPY gold.shipment_fact (
    shipment_id,
    order_date,
    carrier_id,
    origin_hub,
    destination_city,
    service_level,
    promised_delivery_at_utc,
    delivered_at_utc,
    delivery_status,
    shipping_fee_idr,
    refund_amount_idr,
    exception_note
)
FROM '/seed-data/shipment_fact.csv'
WITH (FORMAT csv, HEADER true, NULL '');

COPY gold.carrier_daily (
    metric_date,
    carrier_id,
    shipment_count,
    delivered_count,
    on_time_delivered_count,
    late_delivered_count,
    in_transit_count,
    shipping_fee_idr,
    refund_amount_idr
)
FROM '/seed-data/carrier_daily.csv'
WITH (FORMAT csv, HEADER true);

COPY gold.city_daily (
    metric_date,
    destination_city,
    shipment_count,
    delivered_count,
    on_time_delivered_count,
    late_delivered_count,
    in_transit_count,
    shipping_fee_idr,
    refund_amount_idr
)
FROM '/seed-data/city_daily.csv'
WITH (FORMAT csv, HEADER true);

-- Create strict read-only role for participant/agent queries
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gold_readonly') THEN
        CREATE ROLE gold_readonly LOGIN PASSWORD 'gold_readonly_pass';
    END IF;
END
$$;

ALTER ROLE gold_readonly SET default_transaction_read_only = on;
ALTER ROLE gold_readonly SET TimeZone = 'UTC';

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;

GRANT USAGE ON SCHEMA gold TO gold_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA gold TO gold_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA gold GRANT SELECT ON TABLES TO gold_readonly;
