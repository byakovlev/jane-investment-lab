-- Investment Lab canonical metadata schema (V0.2).
-- Bulk observations live in immutable/versioned Parquet. DuckDB stores catalogue,
-- provenance, security master, corporate actions, quality checks and experiments.

CREATE TABLE IF NOT EXISTS dataset (
    dataset_id BIGINT PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE,
    provider VARCHAR NOT NULL,
    asset_class VARCHAR NOT NULL,
    frequency VARCHAR NOT NULL,
    description VARCHAR,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_version (
    dataset_version_id BIGINT PRIMARY KEY,
    dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id),
    version_label VARCHAR NOT NULL,
    source_asof DATE,
    retrieved_at TIMESTAMPTZ NOT NULL,
    raw_uri VARCHAR NOT NULL,
    source_fingerprint_sha256 VARCHAR NOT NULL,
    canonical_raw_uri VARCHAR,
    canonical_adjusted_uri VARCHAR,
    row_count BIGINT,
    status VARCHAR NOT NULL CHECK (status IN ('INGESTING','VALIDATING','READY','REJECTED')),
    pipeline_version VARCHAR NOT NULL,
    parent_dataset_version_id BIGINT,
    notes VARCHAR,
    UNIQUE(dataset_id, version_label)
);

CREATE TABLE IF NOT EXISTS source_file (
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    relative_path VARCHAR NOT NULL,
    byte_size BIGINT,
    sha256 VARCHAR,
    row_count BIGINT,
    first_observation_date DATE,
    last_observation_date DATE,
    file_role VARCHAR NOT NULL,
    validation_status VARCHAR NOT NULL DEFAULT 'UNVALIDATED'
        CHECK (validation_status IN ('UNVALIDATED','PASS','FAIL','NOT_APPLICABLE')),
    PRIMARY KEY (dataset_version_id, relative_path)
);

CREATE TABLE IF NOT EXISTS ingestion_run (
    ingestion_run_id BIGINT PRIMARY KEY,
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    pipeline_version VARCHAR NOT NULL,
    log_uri VARCHAR,
    summary_json JSON
);

CREATE TABLE IF NOT EXISTS quality_check_result (
    ingestion_run_id BIGINT NOT NULL REFERENCES ingestion_run(ingestion_run_id),
    check_name VARCHAR NOT NULL,
    severity VARCHAR NOT NULL CHECK (severity IN ('INFO','WARNING','ERROR')),
    status VARCHAR NOT NULL CHECK (status IN ('PASS','FAIL','SKIP')),
    observed_value VARCHAR,
    expected_value VARCHAR,
    details_json JSON,
    PRIMARY KEY (ingestion_run_id, check_name)
);

CREATE TABLE IF NOT EXISTS security (
    security_id BIGINT PRIMARY KEY,
    security_name VARCHAR NOT NULL,
    instrument_type VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    first_observed_date DATE NOT NULL,
    last_observed_date DATE NOT NULL,
    is_test BOOLEAN NOT NULL DEFAULT FALSE,
    metadata_completeness VARCHAR NOT NULL DEFAULT 'FULL'
        CHECK (metadata_completeness IN ('FULL','PROVISIONAL')),
    CHECK (last_observed_date >= first_observed_date)
);

-- Stable vendor identity map. One source key always resolves to the same internal security.
CREATE TABLE IF NOT EXISTS source_security_identity (
    provider VARCHAR NOT NULL,
    source_security_key VARCHAR NOT NULL,
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    first_seen_dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    PRIMARY KEY (provider, source_security_key)
);

-- Versioned snapshot of vendor lifecycle metadata. This preserves what each delivery said.
CREATE TABLE IF NOT EXISTS source_security_lifecycle_snapshot (
    provider VARCHAR NOT NULL,
    source_security_key VARCHAR NOT NULL,
    source_dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    terminal_symbol VARCHAR NOT NULL,
    security_name VARCHAR NOT NULL,
    instrument_type VARCHAR NOT NULL,
    exchange_code VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    delisted_at DATE,
    cik VARCHAR,
    figi VARCHAR,
    source_metadata_json JSON,
    PRIMARY KEY (provider, source_security_key, source_dataset_version_id)
);

CREATE TABLE IF NOT EXISTS security_identifier_history (
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    identifier_type VARCHAR NOT NULL,
    identifier_value VARCHAR NOT NULL,
    namespace VARCHAR NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    source_dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    PRIMARY KEY (security_id, identifier_type, namespace, valid_from, source_dataset_version_id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE IF NOT EXISTS corporate_action (
    corporate_action_id BIGINT PRIMARY KEY,
    security_id BIGINT NOT NULL REFERENCES security(security_id),
    action_type VARCHAR NOT NULL CHECK (action_type IN ('SPLIT','DIVIDEND','MERGER','SPINOFF','DELISTING','OTHER')),
    ex_date DATE,
    effective_date DATE NOT NULL,
    announced_at TIMESTAMPTZ,
    safe_to_use_from DATE NOT NULL,
    availability_basis VARCHAR NOT NULL,
    ratio_numerator DOUBLE,
    ratio_denominator DOUBLE,
    cash_amount DOUBLE,
    currency VARCHAR,
    classification VARCHAR,
    source_dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    source_record_id VARCHAR,
    source_payload_json JSON
);

CREATE TABLE IF NOT EXISTS feature_definition (
    feature_id BIGINT PRIMARY KEY,
    name VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    frequency VARCHAR NOT NULL,
    value_type VARCHAR NOT NULL,
    lookback_trading_days INTEGER,
    availability_rule VARCHAR NOT NULL,
    formula_description VARCHAR NOT NULL,
    implementation_hash VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS universe_definition (
    universe_id BIGINT PRIMARY KEY,
    name VARCHAR NOT NULL,
    version INTEGER NOT NULL,
    definition_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS hypothesis (
    hypothesis_id BIGINT PRIMARY KEY,
    hypothesis_text VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_spec (
    strategy_spec_id BIGINT PRIMARY KEY,
    hypothesis_id BIGINT NOT NULL REFERENCES hypothesis(hypothesis_id),
    version INTEGER NOT NULL,
    spec_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    confirmed_at TIMESTAMPTZ,
    UNIQUE(hypothesis_id, version)
);

CREATE TABLE IF NOT EXISTS experiment (
    experiment_id BIGINT PRIMARY KEY,
    strategy_spec_id BIGINT NOT NULL REFERENCES strategy_spec(strategy_spec_id),
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    universe_id BIGINT REFERENCES universe_definition(universe_id),
    engine_version VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status VARCHAR NOT NULL CHECK (status IN ('RUNNING','SUCCEEDED','FAILED')),
    result_uri VARCHAR,
    manifest_json JSON
);

CREATE TABLE IF NOT EXISTS experiment_metric (
    experiment_id BIGINT NOT NULL REFERENCES experiment(experiment_id),
    metric_name VARCHAR NOT NULL,
    metric_value DOUBLE,
    metric_json JSON,
    PRIMARY KEY (experiment_id, metric_name)
);
