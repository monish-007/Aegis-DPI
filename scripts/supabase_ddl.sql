-- Aegis-Pay: Supabase DDL
-- Run this in the Supabase SQL Editor (https://supabase.com/dashboard → SQL Editor)

-- 1. Orders table (idempotency + transaction records)
CREATE TABLE IF NOT EXISTS orders (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key TEXT UNIQUE NOT NULL,
    receipt_id       TEXT,
    external_agent_id TEXT NOT NULL,
    sku             TEXT NOT NULL,
    amount_inr      DOUBLE PRECISION,
    negotiated_price_inr DOUBLE PRECISION NOT NULL,
    razorpay_order_id TEXT,
    razorpay_payment_id TEXT,
    status          TEXT NOT NULL DEFAULT 'SUCCESS',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Audit logs table (JSONB payload)
CREATE TABLE IF NOT EXISTS audit_logs (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Safe upgrade for an existing demo database.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS razorpay_payment_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
-- Compatibility upgrade for the original demo schema.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_agent_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS negotiated_price_inr DOUBLE PRECISION;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS receipt_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS amount_inr DOUBLE PRECISION;
CREATE UNIQUE INDEX IF NOT EXISTS orders_idempotency_key_unique ON orders (idempotency_key);
