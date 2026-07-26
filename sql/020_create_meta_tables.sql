CREATE TABLE meta.build_info (
    build_id UUID NOT NULL,
    built_at_utc TIMESTAMP WITH TIME ZONE NOT NULL,
    source_key VARCHAR NOT NULL,
    dataset_name VARCHAR NOT NULL,
    dataset_version VARCHAR NOT NULL,
    dataset_source_url VARCHAR NOT NULL,
    source_filename VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    source_byte_size BIGINT NOT NULL,
    source_row_count BIGINT NOT NULL,
    loaded_schema VARCHAR NOT NULL,
    loaded_table VARCHAR NOT NULL,
    loaded_row_count BIGINT NOT NULL,
    minimum_date DATE,
    maximum_date DATE,
    git_commit VARCHAR,
    git_worktree_dirty BOOLEAN NOT NULL,
    duckdb_version VARCHAR NOT NULL,
    python_version VARCHAR NOT NULL,
    PRIMARY KEY (build_id, source_key)
);

CREATE TABLE meta.validation_results (
    build_id UUID NOT NULL,
    check_ordinal INTEGER NOT NULL,
    check_code VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('pass', 'warning', 'fail')),
    message VARCHAR NOT NULL,
    metrics JSON NOT NULL,
    PRIMARY KEY (build_id, check_ordinal)
);
