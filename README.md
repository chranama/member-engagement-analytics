# Member Engagement Analytics

A reproducible analytics workflow for identifying transaction-engagement patterns among credit-card-holding customers using the COFINFAD dataset.

## Project overview

This project simulates a realistic analytics assignment for a banking institution. Its purpose is to turn customer and transaction data into a clear, defensible view of engagement that a business partner could use to decide where further investigation or carefully designed outreach may be warranted.

The analysis focuses on customers recorded as holding a credit card and asks how actively they engage with the institution across their available transaction history. It does not attempt to determine whether an individual credit card is active because the dataset does not identify the payment instrument used for each transaction.

## Business context

The project is inspired by a [2021 Incorta case study](https://www.incorta.com/press-releases/redstone-credit-union) describing how Redstone Federal Credit Union's Data Science department connected data from sources such as its core system, card-processing system, and credit bureau. Among the publicly mentioned analytical problems were inactive credit cards and unusual increases in fraudulent activity.


## Analytical objective

The primary objective is to identify and describe meaningful differences in transaction engagement among customers whose `credit_card` field is true.

The analysis will answer:

1. How should transaction engagement be defined using observable behavior?
2. How many credit-card-holding customers exhibit lower engagement under that definition?
3. How do transaction recency, frequency, value, and type differ across engagement groups?
4. Which customer or product characteristics are associated with lower engagement?
5. Which findings are suitable for business action, and which require additional data or validation?

Low engagement will be treated as an analytical signal rather than proof that a customer has stopped using a credit card or intends to leave the institution.

## Dataset

[COFINFAD: Colombian Fintech Financial Analytics Dataset](https://data.mendeley.com/datasets/mhb4zn3258/1) contains anonymized behavioral and transactional data from 48,723 customers of a Colombian fintech company. Its observation period runs from January 4 through December 29, 2023 and includes 3,159,157 transactions.

The analysis will use two source files:

- `customer_data.csv`: customer demographics, product holdings, application activity, satisfaction measures, and derived behavioral attributes
- `transactions_data.csv`: individual transaction dates, amounts, and transaction types linked to customers through `customer_id`

Monetary values are denominated in Colombian pesos. The dataset is available under the CC BY 4.0 license from Mendeley Data. A separate [Hugging Face dataset card](https://huggingface.co/datasets/luisdavidtrejosrojas/cofinfad) provides a field-level data dictionary.

## Local database

The project uses DuckDB as a reproducible local analytical database. Raw CSVs
are loaded into typed, source-faithful tables; the generated database and raw
files are intentionally excluded from Git.

After placing both source files in `data/raw/`, create the environment and run
the database build:

```bash
uv sync --locked
uv run --locked member-engagement-analytics build-database
```

All command-line workflows enter through the same interface. Available
subcommands can be inspected with:

```bash
uv run --locked member-engagement-analytics --help
```

See the [operational runbook](docs/runbook.md) for the complete execution,
recovery, and handoff procedures.

The raw files can be inspected independently before a build:

```bash
uv run --locked member-engagement-analytics preflight
```

The command reruns the complete raw-data preflight, builds a temporary database,
validates the loaded tables, and publishes it only after all blocking checks
pass:

```text
data/processed/member_engagement.duckdb
```

An existing database is protected unless replacement is explicit:

```bash
uv run --locked member-engagement-analytics build-database --replace
```

Analysis code should use the package's read-only connection helper:

```python
from member_engagement_analytics.database import open_database

with open_database() as connection:
    customer_count = connection.execute(
        "SELECT count(*) FROM source.customers"
    ).fetchone()[0]
```

The database contains `source.customers`, `source.transactions`,
`meta.build_info`, and `meta.validation_results`. The `analytics` schema is
reserved for approved analytical objects; no engagement metrics are defined
there yet.

Check the current generated artifact independently of its original build:

```bash
uv run --locked member-engagement-analytics database-health
```

This command opens the database read-only and checks its schema, build
provenance, persisted validation results, row counts, keys, relationships,
ledger totals and dates, duplicate-looking flags, and complete aggregate table
scans. It does not require the raw CSV files and atomically refreshes the
aggregate operational report at:

```text
reports/database-health.json
```

Use `--output` to write the report to a different location.

The test suite validates the build and health logic against controlled
fixtures; the health command answers the separate operational question of
whether the database file currently on disk remains usable and internally
consistent.

## Initial recency analysis

The first completed analysis slice measures overall transaction recency among
recorded credit-card holders and compares the recent 90 days with the preceding
90 days:

```bash
uv run --locked member-engagement-analytics recency-analysis
```

The command reads the DuckDB database without modifying it and reproduces:

- the aggregate recency-scenario table under `reports/tables/`;
- two figures under `reports/figures/`; and
- `reports/cardholder-recency-baseline.md`.

The executed notebook is:

```text
notebooks/01_cardholder_recency_baseline.ipynb
```

The analysis recommends using more than 60 days since the last recorded
transaction as a screening threshold for the next slice, with more than 90
days retained as a nested higher-recency group. This describes overall
transaction recency and does not establish credit-card inactivity.

## Analytical approach

The workflow will:

1. Profile the source files and test record-level and relationship-level data quality.
2. Define a consistent analysis date and observation window.
3. Filter the customer population to recorded credit-card holders.
4. Aggregate transaction history into customer-level recency, frequency, monetary-value, and transaction-mix measures.
5. Create a transparent and reproducible engagement segmentation.
6. Compare engagement groups across relevant customer, product, and behavioral characteristics.
7. Translate the findings into business implications, limitations, and recommended next questions.

Derived fields supplied with COFINFAD, including churn probability and customer lifetime value, will not be treated automatically as ground truth. The project will distinguish source observations from publisher-derived attributes and avoid using a derived outcome to validate a segmentation built from overlapping inputs.

## Planned deliverables

- A validated, customer-level analytical dataset
- Reproducible data preparation and engagement-segmentation logic
- Documented data-quality findings and metric definitions
- Exploratory analysis of engagement patterns and customer segments
- A stakeholder-facing summary or dashboard
- Recommendations that separate supported actions from questions requiring additional evidence

## Scope and limitations

The project can analyze overall transaction engagement among customers recorded as credit-card holders. It cannot directly measure credit-card usage or identify an inactive card because:

- `credit_card` records product ownership but not card status or card-level activity.
- Transaction records do not identify the account, card, or payment instrument involved.
- The data covers approximately one year, limiting longer-term seasonality and lifecycle analysis.
- Observed associations cannot establish why a customer is less engaged.
- Customers of a Colombian fintech company should not be assumed to represent the membership of a particular U.S. banking institution.

Any lower-engagement segment should therefore be treated as a candidate population for validation, not as an automatic marketing, servicing, or risk decision.

## Responsible use

Customer segmentation in a real banking environment would require privacy controls, access governance, validation of sensitive or proxy variables, and review of how recommendations affect different customer groups. Human and domain-expert review would be required before using an analytical segment for customer-facing decisions.

## References

- Muñoz Guerrero, L. E., Ceballos, Y. F., and Trejos Rojas, L. D. [COFINFAD: Colombian Fintech Financial Analytics Dataset](https://data.mendeley.com/datasets/mhb4zn3258/1).
- Incorta. [Redstone Credit Union Leverages Incorta as Data Hub for Data Science Department](https://www.incorta.com/press-releases/redstone-credit-union), April 14, 2021.
