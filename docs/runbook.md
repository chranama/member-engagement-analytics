# Operational Runbook

## Purpose

This runbook describes how to inspect the raw COFINFAD files, build and verify
the DuckDB database, reproduce the current analysis, and recover from common
failures.

Run every command from the repository root. All project workflows enter through
the centralized `member-engagement-analytics` CLI.

## Default paths

| Resource | Default path | Git policy |
| --- | --- | --- |
| Customer source | `data/raw/customer_data.csv` | Ignored |
| Transaction source | `data/raw/transactions_data.csv` | Ignored |
| Analytical database | `data/processed/member_engagement.duckdb` | Ignored |
| Preflight report | `reports/preflight-data.json` | Tracked |
| Database-health report | `reports/database-health.json` | Ignored |
| Recency findings memo | `reports/cardholder-recency-baseline.md` | Tracked |
| Recency tables | `reports/tables/` | Tracked |
| Recency figures | `reports/figures/` | Tracked |

## Initial setup

The project requires Python 3.12 and uses `uv` for environment and dependency
management.

```bash
uv sync --locked
uv run --locked member-engagement-analytics --help
```

Do not run project modules with an unqualified system Python. If
`uv sync --locked` reports that the lockfile is stale, review the
`pyproject.toml` change and update the lockfile intentionally rather than
dropping `--locked`.

## Standard workflow

### 1. Inspect the raw files

```bash
uv run --locked member-engagement-analytics preflight
```

The preflight scans the raw CSV files without creating or modifying a database.
It verifies file identity, headers, row counts, parseability, required values,
categories, ranges, relationships, and publisher-derived aggregates.

Expected artifact:

```text
reports/preflight-data.json
```

Warnings are recorded but do not block the workflow. Any blocking failure must
be resolved before building the database.

### 2. Build the database

For the first build:

```bash
uv run --locked member-engagement-analytics build-database
```

If the target already exists and replacement is intentional:

```bash
uv run --locked member-engagement-analytics build-database --replace
```

The build reruns the raw-data preflight, creates and validates a temporary
DuckDB file, verifies it through a read-only connection, and only then
publishes it at:

```text
data/processed/member_engagement.duckdb
```

Without `--replace`, an existing target is protected. A failed replacement
leaves the existing database unchanged.

### 3. Inspect database health

```bash
uv run --locked member-engagement-analytics database-health
```

The health command opens the database read-only and checks its structure,
metadata, persisted validations, row counts, keys, relationships, ledger
baseline, duplicate-looking flags, and complete aggregate table scans.

It atomically refreshes:

```text
reports/database-health.json
```

The health report is an operational snapshot and is intentionally ignored by
Git. Rerun the command whenever the database is rebuilt or before relying on an
older local database.

### 4. Reproduce the recency analysis

```bash
uv run --locked member-engagement-analytics recency-analysis
```

The analysis reads the database without modifying it and refreshes:

- `reports/tables/cardholder-recency-scenarios.csv`
- `reports/figures/cardholder-recency-distribution.png`
- `reports/figures/cardholder-prior-vs-recent-activity.png`
- `reports/cardholder-recency-baseline.md`

Review the Git diff after regeneration. Unexpected changes should be explained
before committing analytical artifacts.

### 5. Run project verification

Fast checks:

```bash
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked pytest -q
uv lock --check
```

Opt-in tests against the complete local COFINFAD files:

```bash
RUN_FULL_DATA_TESTS=1 uv run --locked pytest -q -m full_data
```

## Exit codes

Warnings do not produce a nonzero exit code.

| Command | `0` | `1` | `2` |
| --- | --- | --- | --- |
| `preflight` | Inspection completed with no blocking failures | Blocking source-data failure | Configuration or runtime failure |
| `build-database` | Build completed with no blocking failures | Source or post-load validation failure | Filesystem, SQL, configuration, or runtime failure |
| `database-health` | Health inspection completed with no blocking failures | Current database failed a health check | Configuration or runtime failure |
| `recency-analysis` | Analysis and outputs completed | Analysis or runtime failure | Not currently used |

## Interpreting warnings

Warnings identify conditions requiring interpretation, not automatic cleaning.
The known COFINFAD findings include:

- exact duplicate-looking transaction groups;
- an extreme upper tail in transactions per customer; and
- disagreement between some publisher-derived customer fields and aggregates
  calculated from the transaction ledger.

Database health may also warn that the database was built from a Git worktree
with uncommitted changes. Rebuild from a clean commit when clean provenance is
required.

Do not delete, cap, deduplicate, or overwrite source values solely to eliminate
a warning. Cleaning requires a documented analytical or business rule.

## Recovery procedures

### Preflight fails

1. Read the failing check code in the console or `preflight-data.json`.
2. Confirm both expected filenames are present under `data/raw/`.
3. For checksum, byte-size, header, or row-count failures, reacquire the source
   files rather than editing them manually.
4. Rerun preflight.
5. Do not build until blocking failures are resolved.

### The database target already exists

1. Run `database-health` against the existing target.
2. Preserve it if it is healthy and no rebuild is required.
3. Use `build-database --replace` only when replacement is intentional.

### A database build fails

1. Read the reported source or post-load check.
2. Run preflight independently if the problem originates in the raw files.
3. Correct code, configuration, or source placement as appropriate.
4. Confirm the prior database still passes `database-health`.
5. Retry the build.

### Database health fails

1. Do not treat downstream analysis outputs as current.
2. Inspect the failing health check in `reports/database-health.json`.
3. Rerun preflight to verify the source contract.
4. Rebuild with `build-database --replace`.
5. Rerun `database-health`.
6. Reproduce the analysis only after health returns no blocking failures.

### Recency analysis fails

1. Run `database-health`.
2. Confirm the database path and analysis SQL files exist.
3. Run the unit tests to identify a violated analysis contract.
4. Resolve the failure and rerun the analysis.
5. Review all regenerated artifacts before committing.

## Custom paths

Every workflow supports explicit input or output paths:

```bash
uv run --locked member-engagement-analytics preflight \
    --raw-dir /path/to/raw \
    --output /path/to/preflight.json

uv run --locked member-engagement-analytics build-database \
    --raw-dir /path/to/raw \
    --target /path/to/analysis.duckdb

uv run --locked member-engagement-analytics database-health \
    --database /path/to/analysis.duckdb \
    --output /path/to/database-health.json

uv run --locked member-engagement-analytics recency-analysis \
    --database /path/to/analysis.duckdb
```

## Handoff checklist

Before handing off or committing a completed run, confirm:

- preflight has no blocking failures;
- database health has no blocking failures;
- the recency analysis completes;
- unit, lint, formatting, and applicable full-data tests pass;
- tracked report changes are expected and reviewed;
- raw CSVs, DuckDB files, and the database-health snapshot remain untracked;
  and
- any remaining warnings are documented rather than silently corrected.
