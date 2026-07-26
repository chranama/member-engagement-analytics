# Database Build Implementation Plan

## Objective

Build a reproducible DuckDB file from the two validated COFINFAD CSV files:

```text
data/processed/member_engagement.duckdb
```

The completed database will contain typed, source-faithful tables, build provenance, and validation results. Analysis code will open the completed file read-only.

## Build contract

The build will have two modes:

1. **Controlled write mode:** one command validates the raw files and creates a replacement database in a temporary file.
2. **Read-only analysis mode:** notebooks, reports, and analytical code can query the completed database but cannot modify it.

Primary command:

```bash
uv run --locked python -m member_engagement_analytics.build_database
```

Rebuilding an existing target will require an explicit flag:

```bash
uv run --locked python -m member_engagement_analytics.build_database --replace
```

Exit codes will match the preflight convention:

- `0`: build completed; warnings may be present
- `1`: source-data or post-load validation failure
- `2`: configuration, filesystem, SQL, or other runtime failure

## Proposed files

```text
src/member_engagement_analytics/
├── build_database.py   # CLI and build orchestration
├── database.py         # paths, SQL execution, build lifecycle, connections
├── preflight.py
├── reporting.py
└── validation.py

sql/
├── 001_create_schemas.sql
├── 010_create_source_tables.sql
├── 020_create_meta_tables.sql
├── 100_load_customers.sql
└── 110_load_transactions.sql

tests/
├── fixtures/
│   ├── customer_data.csv
│   └── transactions_data.csv
├── test_database.py
└── test_preflight.py
```

The implementation may extract shared dataset constants and repository-relative paths from `validation.py` into `config.py` if both preflight and database code otherwise duplicate them. It should not introduce a broad `utils.py`.

## Database objects

Create these schemas:

```text
source
analytics
meta
```

The `analytics` schema will initially be empty. Creating it now establishes the database boundary without introducing undefined analytical metrics.

### `source.customers`

- One row per `customer_id`
- Every source column retained
- Explicit DuckDB types from the database specification
- Empty CSV fields represented as SQL `NULL`
- `customer_id BIGINT PRIMARY KEY`
- Ambiguous publisher-derived fields retained for lineage, not silently corrected

### `source.transactions`

- One row per source CSV row
- Every source row retained, including duplicate-looking rows
- `transaction_row_id BIGINT PRIMARY KEY`
- `customer_id BIGINT NOT NULL`
- `transaction_date DATE NOT NULL`
- `amount_cop DECIMAL(20,2) NOT NULL`
- `transaction_type VARCHAR NOT NULL`
- Foreign key to `source.customers(customer_id)`
- `is_duplicate_looking BOOLEAN NOT NULL`

`transaction_row_id` will be assigned from deterministic CSV row order during the load. The duplicate flag will mark every member of an exact customer/date/amount/type duplicate group; it will not remove a row.

### `meta.build_info`

Store one row per source file per build:

- Build ID
- UTC build timestamp
- Dataset name, version, and source URL
- Source key and filename
- SHA-256 checksum and byte size
- Source and loaded row counts
- Loaded table name
- Minimum and maximum applicable dates
- Git commit when available
- DuckDB and Python versions

### `meta.validation_results`

Persist aggregate validation results:

- Build ID
- Check code
- Status: `pass`, `warning`, or `fail`
- Human-readable message
- Aggregate metrics as DuckDB `JSON`

No customer identifier or source row should be stored in validation metadata.

## Warning-handling decisions

The build must preserve the conclusions of the preflight:

| Finding | Build behavior |
| --- | --- |
| 102 excess duplicate-looking transaction rows | Retain every row and set `is_duplicate_looking`; do not deduplicate |
| Extreme transaction-count upper tail | Record the warning in `meta.validation_results`; do not remove or cap customers |
| Publisher-derived field disagreements | Retain all source fields; do not use the ambiguous secondary fields to validate the load |

The later analytical layer should calculate canonical transaction metrics from `source.transactions`. It should not rely on `average_transaction_value`, `total_transaction_volume`, or `last_transaction_date` without establishing their semantics.

## Implementation phases

### Phase 0: Establish the implementation baseline

1. Keep the current preflight implementation and `uv.lock` as the source contract.
2. Add generated database files and DuckDB write-ahead-log files to `.gitignore`.
3. Update the database specification to use the package command rather than `src/build_database.py`.
4. Confirm the preflight passes with zero blocking failures before starting a build.

Gate: preflight, tests, lint, formatting, and lockfile checks all pass.

### Phase 1: Add SQL schema definitions

1. Create the three schemas.
2. Define `source.customers` with every column and explicit type.
3. Define `source.transactions`, including the surrogate key and duplicate flag.
4. Define the two metadata tables.
5. Keep all DDL idempotent within a new empty build database.

Gate: the SQL creates the expected schemas, tables, columns, constraints, and types in a temporary test database.

### Phase 2: Implement the atomic build lifecycle

1. Resolve raw, target, SQL, and temporary paths from the repository root.
2. Refuse to overwrite an existing database unless `--replace` is present.
3. Run the raw-file preflight directly; do not trust a previously generated JSON report.
4. Create a uniquely named temporary database beside the target.
5. Create schemas and tables.
6. Load customers before transactions.
7. Run all post-load validations.
8. Write provenance and validation results.
9. Execute `CHECKPOINT`, close every connection, and verify the temporary database read-only.
10. Atomically replace the target only after every blocking validation passes.
11. Delete only the known temporary build file after a failed build.

Gate: a failed build leaves any existing target unchanged and does not leave a partial database at the target path.

### Phase 3: Implement typed source loads

1. Read both CSVs as temporary all-text relations.
2. Cast each column explicitly in SQL.
3. Normalize empty strings to `NULL`.
4. Preserve all customer columns.
5. Rename transaction `date`, `amount`, and `type` to unambiguous database names.
6. Assign stable sequential transaction row IDs.
7. Calculate the duplicate-looking flag without deleting rows.
8. Avoid CSV type inference in permanent tables.

Gate:

- `source.customers` contains 48,723 rows.
- `source.transactions` contains 3,159,157 rows.
- Every source row is represented exactly once.

### Phase 4: Add post-load validation

Blocking validations:

- Expected schemas, tables, columns, and types exist.
- Loaded row counts equal raw source counts.
- Customer IDs are non-null and unique.
- Transaction row IDs are non-null, unique, and sequential.
- Required transaction fields are non-null.
- Every transaction customer resolves to a customer.
- Loaded transaction amount total equals the raw CSV total.
- Loaded minimum and maximum transaction dates equal the raw CSV values.
- No cast failures were silently converted to null.

Nonblocking validations:

- Duplicate-looking row and group counts
- Missing optional customer attributes
- Transaction-count concentration
- Publisher-derived aggregate disagreements

Gate: any blocking failure prevents replacement of the target database.

### Phase 5: Enforce read-only consumption

Implement in `database.py`:

```python
def open_database(
    database_path: Path | None = None,
    *,
    read_only: bool = True,
) -> duckdb.DuckDBPyConnection:
    ...
```

Analysis callers must receive a read-only connection by default. Write mode should remain private to the controlled build implementation.

Gate:

- A smoke query can read both source tables.
- `CREATE`, `INSERT`, `UPDATE`, and `DELETE` fail through the public connection helper.

### Phase 6: Add automated tests

Fast fixture tests:

- Successful build from small valid CSV fixtures
- Missing source file
- Unexpected header
- Type-cast failure
- Duplicate customer key
- Orphan transaction
- Duplicate-looking transactions retained and flagged
- Existing target protected without `--replace`
- Failed replacement preserves the prior target
- Read-only connection rejects writes
- Metadata contains no source identifiers

Full-data test:

- Build from the downloaded COFINFAD files
- Verify all published row counts and totals
- Verify the three known warnings
- Run two independent temporary builds and compare logical fingerprints

Logical reproducibility should compare schemas, row counts, sums, date ranges, and deterministic table fingerprints. The binary database file should not be expected to have an identical SHA-256 because build timestamps and storage details may differ.

Gate: `pytest`, Ruff lint, Ruff formatting, and the full-data build all pass.

### Phase 7: Update user-facing documentation

1. Add environment setup and database-build commands to the README.
2. Document `--replace`, exit codes, target path, and read-only usage.
3. Explain that raw CSVs and the generated database are intentionally uncommitted.
4. Update `docs/database-spec.md` where implementation decisions supersede provisional language.

Gate: a clean checkout plus locally downloaded raw data can reproduce the database using documented commands.

## Build summary output

The successful CLI should report only aggregate operational information:

```text
Database build: PASS WITH WARNINGS
Target: data/processed/member_engagement.duckdb
Customers loaded: 48,723
Transactions loaded: 3,159,157
Blocking failures: 0
Warnings: 3
Read-only verification: passed
```

It must not print customer identifiers or source rows.

## Definition of done

The database build is complete when:

- One command creates the database from validated raw CSVs.
- The target is replaced atomically and only after validation.
- All source rows and source columns are preserved.
- No warning causes an undocumented mutation or row deletion.
- Build provenance and aggregate validation results are queryable.
- Analysis connections are read-only by default.
- Fixture tests and the full-data build pass.
- A repeat build produces the same logical contents.
- Raw CSVs, temporary build files, and generated DuckDB files remain outside Git.
