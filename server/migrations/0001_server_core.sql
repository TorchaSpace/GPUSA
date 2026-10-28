-- Server-owned tables: who may talk to the server and what they sent.
-- The business tables (products, sales, stock, ...) are ported from
-- database/schema.sql in the next migrations; this one is what the sync
-- layer needs first.

CREATE EXTENSION IF NOT EXISTS citext;

-- One row per till / depot terminal / admin PC. Only a hash of its token is
-- stored; the token itself is shown once when the device is enrolled.
CREATE TABLE devices (
    id              UUID PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('pos', 'depot', 'admin')),
    dealership_code CITEXT,
    name            TEXT NOT NULL,
    token_hash      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

-- Events pushed from the tills' outbox. event_id is made by the client, so a
-- re-sent event hits the primary key and is stored once (idempotent push).
CREATE TABLE sync_events (
    event_id          UUID PRIMARY KEY,
    device_id         UUID NOT NULL REFERENCES devices (id),
    kind              TEXT NOT NULL,
    payload           JSONB NOT NULL,
    client_created_at TIMESTAMPTZ NOT NULL,
    received_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    status            TEXT NOT NULL DEFAULT 'accepted' CHECK (status IN ('accepted', 'rejected')),
    reject_reason     TEXT
);
CREATE INDEX sync_events_device_received ON sync_events (device_id, received_at);
