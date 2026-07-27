# Cardholder Transaction Recency Baseline

- **Status:** Complete
- **Dataset:** COFINFAD, version 1
- **Analysis date:** December 29, 2023
- **Eligible population:** Customers recorded as credit-card holders

## Purpose

For a banking institution, an extended gap in transaction activity may signal a
weakening customer relationship, reduced reliance on the institution, or a
service need worth investigating. Identifying that gap early can help the
business decide whether further retention, engagement, or customer-experience
analysis is warranted. In this dataset, however, transactions are not linked to
a particular card or account, so the analysis concerns overall relationship
activity rather than credit-card use.

The broader business question is whether customers recorded as credit-card
holders exhibit meaningful signs of reduced transaction engagement, how large
and distinct that population is, and whether the relevant pattern is persistent
light use, recent decline, or apparent lapse.

This first slice addresses the earliest part of that question by measuring
transaction recency and placing it in the context of immediately preceding
activity. It will determine whether a potentially lapsed population exists at
an operationally meaningful size and which recency threshold provides a
defensible starting point for further investigation.

The result will enable subsequent analysis to:

- distinguish historically light customers from customers whose activity has
  recently declined;
- define a bounded cohort for customer, product, application, service, and
  satisfaction profiling;
- identify additional card-, account-, and relationship-level data needed
  before recommending action; and
- define candidate measures for ongoing engagement monitoring.

## Primary question

> As of December 29, 2023, how many recorded credit-card holders have gone 30,
> 60, or 90 days without a transaction, and were those customers meaningfully
> active beforehand?

Supporting questions are:

1. What is the distribution of transaction recency?
2. How many customers exceed each candidate recency threshold?
3. How much transaction activity did those customers have in the preceding
   90-day period?
4. Do the recency groups primarily represent previously active customers,
   consistently light users, or customers with limited observable history?
5. Do duplicate-looking transaction records materially change the aggregate
   results?

## Decision produced

This slice will support three decisions:

1. Whether a 30-, 60-, or 90-day threshold is the most useful starting
   definition of apparent lapse
2. Whether apparent lapse and recent decline should be analyzed as distinct
   patterns
3. Whether the next analysis slice should examine activity trajectory or
   profile the selected recency group

It will not establish a final engagement segmentation.

## Source contract

The analysis will query:

```text
data/processed/member_engagement.duckdb
```

Required source tables are:

- `source.customers`
- `source.transactions`

All dates and transaction counts will be calculated from
`source.transactions`. Publisher-derived customer transaction fields will not
be used.

The canonical analysis will retain every source transaction. An alternative
query that collapses exact customer/date/amount/type groups to one analytical
event will be used only as a sensitivity check.

## Population

Include customers where:

```sql
source.customers.credit_card IS TRUE
```

The expected population is 30,460 customers. Every eligible customer has at
least 10 recorded transactions, so this dataset does not contain a
transaction-zero credit-card-holder group.

The analysis concerns overall recorded transaction activity. It cannot
determine whether a credit card itself was used or is inactive.

## Analysis date and windows

Use December 29, 2023, the maximum source transaction date, as the fixed
analysis date.

| Period | Inclusive dates | Purpose |
| --- | --- | --- |
| Recent 30 days | November 30–December 29 | Candidate short lapse interval |
| Recent 60 days | October 31–December 29 | Candidate medium lapse interval |
| Recent 90 days | October 1–December 29 | Candidate long lapse interval |
| Prior 90 days | July 3–September 30 | Activity context for the recent period |

All calculations must use these fixed dates rather than the date on which the
analysis code runs.

## Analytical grain

The working query will return one row per eligible customer. It will contain
customer identifiers for internal reconciliation, but all exported tables and
figures will be aggregate.

This slice does not require a permanent analytics table. The metric query will
remain version-controlled SQL until the definitions have been evaluated.

## Working metrics

Calculate only:

| Metric | Definition |
| --- | --- |
| `first_transaction_date` | Customer's minimum ledger transaction date |
| `last_transaction_date` | Customer's maximum ledger transaction date |
| `observed_days` | Inclusive days from first transaction through December 29 |
| `recency_days` | Days from last transaction through December 29 |
| `transactions_full_period` | Customer's complete ledger transaction count |
| `transactions_recent_90d` | Transactions from October 1 through December 29 |
| `transactions_prior_90d` | Transactions from July 3 through September 30 |
| `active_months` | Distinct calendar months containing a transaction |

Also calculate:

```text
sufficient_prior_history =
    first_transaction_date <= July 3, 2023
```

This flag prevents customers with limited observable history from being
interpreted as having declined.

## Recency scenarios

Report the following overlapping threshold scenarios:

| Scenario | Rule |
| --- | --- |
| More than 30 days | `recency_days > 30` |
| More than 60 days | `recency_days > 60` |
| More than 90 days | `recency_days > 90` |

For distribution charts, use mutually exclusive bands:

- 0–30 days
- 31–60 days
- 61–90 days
- More than 90 days

The executed analysis produced:

| Scenario | Customers | Eligible population |
| --- | ---: | ---: |
| More than 30 days | 5,835 | 19.2% |
| More than 60 days | 1,686 | 5.5% |
| More than 90 days | 457 | 1.5% |

These values are contextualized in the findings memo and do not constitute a
definition of credit-card inactivity.

## Analysis method

### 1. Build and validate the working relation

- Filter the customer table to recorded credit-card holders.
- Aggregate transaction dates and counts by customer.
- Calculate fixed-window counts using conditional aggregation.
- Join the aggregates back to the eligible population.
- Confirm one row per eligible customer.

### 2. Describe recency

Report:

- minimum, median, interquartile range, 90th percentile, 95th percentile, and
  maximum recency;
- counts and percentages for the mutually exclusive recency bands; and
- an empirical cumulative distribution of `recency_days`.

### 3. Add prior-activity context

For each recency band and overlapping threshold scenario, report:

- customer count and population share;
- median and interquartile range of `transactions_prior_90d`;
- percentage with zero prior-period transactions;
- median `transactions_full_period`;
- median `active_months`; and
- percentage lacking sufficient prior history.

This describes whether an apparently lapsed group had meaningful earlier
activity without assigning a final decline label.

### 4. Compare recent and prior activity

For customers with sufficient prior history, compare
`transactions_recent_90d` with `transactions_prior_90d` using:

- median counts in each period;
- median absolute change;
- percentage whose recent count is lower, equal, or higher; and
- a bounded scatterplot or two-dimensional count plot.

Because transaction counts have an extreme upper tail, charts will use bounded
axes, logarithmic scales, or aggregated bins. Means will not be the primary
summary.

### 5. Run the duplicate sensitivity check

Repeat the threshold counts and prior-period summaries after collapsing exact
duplicate-looking groups to one analytical event. Report the absolute and
percentage change from the canonical results.

The sensitivity query will not modify `source.transactions`.

## Outputs

### SQL

```text
sql/analysis/010_cardholder_recency_baseline.sql
```

The query will return the one-row-per-cardholder working relation.

### Notebook

```text
notebooks/01_cardholder_recency_baseline.ipynb
```

The notebook will contain reconciliation, aggregate analysis, figures,
sensitivity results, and concise interpretation.

### Aggregate table

```text
reports/tables/cardholder-recency-scenarios.csv
```

The table will contain threshold counts, population percentages, prior-period
activity summaries, and limited-history percentages.

### Figures

```text
reports/figures/cardholder-recency-distribution.png
reports/figures/cardholder-prior-vs-recent-activity.png
```

### Findings memo

```text
reports/cardholder-recency-baseline.md
```

The memo will summarize the result, recommend whether one recency threshold
should be carried into the next slice, and state what the data cannot establish.

No dashboard is required.


## Quality requirements

The analysis must confirm:

- exactly 30,460 working rows;
- a non-null, unique customer identifier;
- `first_transaction_date <= last_transaction_date <= December 29, 2023`;
- nonnegative recency and transaction counts;
- full-period counts greater than or equal to both 90-day counts;
- scenario counts that reproduce the preliminary baseline;
- mutually exclusive recency bands that sum to the eligible population;
- results with and without duplicate-looking groups;
- deterministic aggregate exports; and
- no customer identifier in an exported table, figure, or memo.

## Completion criteria

This analysis slice is complete when:

- the version-controlled SQL and notebook reproduce the eligible population;
- all three recency scenarios and the recency distribution are documented;
- prior-period activity is reported for each recency group;
- limited-history customers are explicitly separated in interpretation;
- duplicate sensitivity is quantified;
- the two required figures and aggregate scenario table are produced;
- the findings memo recommends the next analytical decision; and
- the interpretation consistently refers to overall transaction recency rather
  than credit-card inactivity.
