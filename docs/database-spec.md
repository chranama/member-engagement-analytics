# Analytical Database Specification

## Decision

Use **DuckDB 1.4 LTS** as the local analytical database.

SQLite is capable of storing and querying this dataset, but DuckDB is the better fit for the expected workload:

- The database is local, embedded, and used without a server.
- The workload is read-heavy and dominated by scans, joins, aggregations, date calculations, and window functions.
- The transaction table contains 3,159,157 rows, while the result sets consumed by Python should normally be much smaller aggregates.
- DuckDB provides native `DATE`, `BOOLEAN`, and `DECIMAL` types and analytical SQL features that SQLite either lacks or implements less directly.
- DuckDB can be opened explicitly in read-only mode and queried directly from Python.

SQLite remains a reasonable fallback if minimizing dependencies or practicing SQLite specifically becomes a project objective. Read-only access alone is not a reason to prefer it over an analytical engine.

## Scope

The database is a reproducible local analytical artifact built from the two COFINFAD CSV files. It is not a transactional application database and will not accept user-generated writes.

The lifecycle has two distinct modes:

1. **Build mode:** a controlled script recreates the database from raw source files.
2. **Analysis mode:** notebooks, SQL queries, tests, and reports open the completed database read-only.

The database file will be:

```text
data/processed/member_engagement.duckdb
```

The database is derived from publicly available source data and should not be committed to Git. The raw CSV files and the database can both be recreated locally.

## Source data

| Source file | Expected data rows | Expected bytes | SHA-256 |
| --- | ---: | ---: | --- |
| `data/raw/customer_data.csv` | 48,723 | 23,519,477 | `bb3865b68c247caaa28821238c3d9fa9c745ca8837c42f89b5d7e310beb98c8d` |
| `data/raw/transactions_data.csv` | 3,159,157 | 119,006,497 | `09fa21b8d74692cbfbf10ee58b55b00c874280a57aa926650e2fe55c60859ec6` |

The build must fail if either file is missing, unreadable, or has an unexpected header.

## Database organization

Use three schemas:

| Schema | Purpose |
| --- | --- |
| `source` | Typed, source-faithful tables loaded from COFINFAD |
| `analytics` | Reusable views or materialized tables created for the analysis |
| `meta` | Build provenance, source checksums, row counts, and validation results |

Initial objects:

```text
source.customers
source.transactions
meta.build_info
meta.validation_results
```

Analytical objects such as `analytics.customer_transaction_summary` should be added only after their metric definitions and observation windows are approved in the analysis brief.

## Source table design

### `source.customers`

Grain: one row per `customer_id`.

| Column group | DuckDB type | Notes |
| --- | --- | --- |
| `customer_id` | `BIGINT` | Primary key |
| Age, household, product-count, login, feature, transaction-count, and support-count fields | `INTEGER` | Must parse as whole numbers when present |
| Product and feature indicator fields | `BOOLEAN` | Parse only `True`, `False`, or null |
| Transaction and survey date fields | `DATE` | Parse ISO `YYYY-MM-DD` values |
| Source transaction totals and volumes expressed as whole COP | `DECIMAL(20,2)` | Preserve exact financial arithmetic |
| Publisher-derived averages, customer lifetime value, ratios, frequencies, satisfaction components, tenure, and churn probability | `DOUBLE` | Preserve the source's published floating-point values without rounding them during ingestion |
| NPS and integer satisfaction scores | `INTEGER` | Retain published scale values |
| All categorical and free-text fields | `VARCHAR` | Empty CSV fields become SQL `NULL` |

The build DDL must enumerate every source column explicitly. It must not rely on permanent CSV type inference because inferred types can change when the data changes.

### `source.transactions`

Grain: one source transaction record.

| Column | DuckDB type | Constraint or rule |
| --- | --- | --- |
| `transaction_row_id` | `BIGINT` | Stable surrogate assigned from source-file row order; primary key |
| `customer_id` | `BIGINT` | Not null; must resolve to `source.customers.customer_id` |
| `transaction_date` | `DATE` | Not null; renamed from source column `date` |
| `amount_cop` | `DECIMAL(20,2)` | Not null; renamed from `amount` and explicitly denominated in COP |
| `transaction_type` | `VARCHAR` | Not null; renamed from source column `type` |
| `is_duplicate_looking` | `BOOLEAN` | Not null; marks every row in an exact customer/date/amount/type duplicate group |

The source has no transaction identifier. Exact duplicate-looking rows must therefore be preserved unless a later business rule supplies evidence that they are duplicate events.

## Build process

The database-build command is:

```bash
uv run --locked python -m member_engagement_analytics.build_database
```

Rebuilding an existing target requires explicit replacement:

```bash
uv run --locked python -m member_engagement_analytics.build_database --replace
```

The command:

1. Resolve all paths relative to the repository root.
2. Verify the source filenames, headers, sizes, and SHA-256 checksums.
3. Build a new temporary database rather than modifying the current database in place.
4. Create schemas and tables with explicit types.
5. Load and cast the source data.
6. Run the required validation checks.
7. Record provenance in `meta.build_info`.
8. Record aggregate source and post-load checks in `meta.validation_results`.
9. Checkpoint, close, and verify the database through a read-only connection.
10. Replace the prior generated database only after every validation passes.

This atomic-build pattern prevents a failed load from leaving a partially valid database at the expected production path.

## Required validations

The build must fail on:

- A missing or unexpected source column
- A source row that cannot be parsed into the declared type
- A null or duplicate customer primary key
- A null transaction customer, date, amount, or type
- A transaction whose `customer_id` is absent from `source.customers`
- A mismatch between source and loaded row counts
- A mismatch between source and loaded total transaction amount

The build must report, but not automatically fail on:

- Exact duplicate-looking transaction records
- Missing optional customer attributes
- Values outside expected business ranges
- Disagreement between transaction-ledger aggregates and publisher-derived customer fields such as `tx_count`, `total_tx_volume`, or `last_transaction_date`

Those conditions may be data-quality findings rather than load failures and must not be silently corrected.

## Provenance

`meta.build_info` must record at least:

- Dataset name and version
- Source URL
- Source filename
- Source SHA-256 checksum
- Source byte size
- Source data-row count
- Loaded table name
- Loaded row count
- Minimum and maximum transaction dates where applicable
- Database-build timestamp in UTC
- Build-script version or Git commit when available
- Whether the Git worktree contained uncommitted changes at build time

`meta.validation_results` records each aggregate check code, status, message,
and JSON metrics. It does not store customer rows or customer identifiers.

## Access contract

Only the database-build script may open the database in write mode.

All analysis code must use an explicit read-only connection:

```python
from member_engagement_analytics.database import open_database

connection = open_database()
```

Analysis code must not issue `CREATE`, `INSERT`, `UPDATE`, `DELETE`, `DROP`, or `ALTER` statements. Temporary Python data frames are acceptable; persistent analytical database objects belong in the controlled build.

## Query and performance policy

- Perform joins, filtering, grouping, and large scans in DuckDB.
- Transfer only analysis-ready aggregates or bounded samples into pandas.
- Do not create manual indexes initially. DuckDB is columnar and provides automatic data-skipping metadata; add an index only after measuring a specific query problem.
- Use `EXPLAIN ANALYZE` before adding performance-oriented complexity.
- Keep database-build SQL under `sql/db_build/` and analytical SQL under
  `sql/analysis/`.
- Use deterministic ordering whenever row order affects an exported result.

## Security and portability

- The database contains public, anonymized portfolio data, not production banking data.
- Paths must be repository-relative; no developer-specific absolute paths may appear in code.
- Raw CSVs and generated database files remain uncommitted.
- The build must not require network access after the two source CSVs have been downloaded.
- A future migration to PostgreSQL or a cloud warehouse would require dialect review, but the logical source and analytical grains should remain portable.

## Acceptance criteria

The database layer is complete when:

- One documented command creates the database from the raw CSV files.
- Re-running the build produces the same schemas, row counts, and analytical values.
- Required validations pass.
- A Python smoke test opens the database read-only and successfully queries both source tables.
- Attempts to modify the database through the analysis connection fail.
- No raw or generated database file appears in Git status.
