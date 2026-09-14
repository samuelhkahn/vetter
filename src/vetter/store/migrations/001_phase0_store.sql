CREATE TABLE nights (
    observing_date DATE PRIMARY KEY,
    source_url TEXT NOT NULL,
    archive_byte_size BIGINT NOT NULL CHECK (archive_byte_size >= 0),
    archive_sha256 TEXT NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    parquet_path TEXT NOT NULL,
    parquet_byte_size BIGINT NOT NULL CHECK (parquet_byte_size >= 0),
    parquet_sha256 TEXT NOT NULL CHECK (parquet_sha256 ~ '^[0-9a-f]{64}$'),
    minimum_night_alerts INTEGER NOT NULL CHECK (minimum_night_alerts > 0),
    manifest JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('loading', 'complete', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE alerts (
    candid BIGINT PRIMARY KEY,
    observing_date DATE NOT NULL REFERENCES nights(observing_date),
    object_id TEXT NOT NULL CHECK (length(object_id) > 0),
    observation_jd DOUBLE PRECISION NOT NULL CHECK (observation_jd > 0),
    ra DOUBLE PRECISION NOT NULL CHECK (ra >= 0 AND ra < 360),
    dec DOUBLE PRECISION NOT NULL CHECK (dec >= -90 AND dec <= 90),
    broker_probabilities JSONB CHECK (
        broker_probabilities IS NULL
        OR jsonb_typeof(broker_probabilities) = 'object'
    ),
    original_payload BYTEA NOT NULL CHECK (octet_length(original_payload) > 0),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (observing_date, candid)
);

CREATE INDEX alerts_night_object_idx
    ON alerts (observing_date, object_id, observation_jd, candid);

CREATE TABLE candidates (
    observing_date DATE NOT NULL REFERENCES nights(observing_date),
    object_id TEXT NOT NULL CHECK (length(object_id) > 0),
    candid BIGINT NOT NULL,
    observation_jd DOUBLE PRECISION NOT NULL CHECK (observation_jd > 0),
    ra DOUBLE PRECISION NOT NULL CHECK (ra >= 0 AND ra < 360),
    dec DOUBLE PRECISION NOT NULL CHECK (dec >= -90 AND dec <= 90),
    broker_probabilities JSONB CHECK (
        broker_probabilities IS NULL
        OR jsonb_typeof(broker_probabilities) = 'object'
    ),
    eligible_history JSONB NOT NULL CHECK (jsonb_typeof(eligible_history) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (observing_date, object_id),
    FOREIGN KEY (observing_date, candid)
        REFERENCES alerts(observing_date, candid)
);

CREATE TABLE processing_runs (
    run_id TEXT PRIMARY KEY CHECK (length(run_id) > 0),
    observing_date DATE NOT NULL REFERENCES nights(observing_date),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ
);

CREATE INDEX processing_runs_night_idx
    ON processing_runs (observing_date, started_at, run_id);
