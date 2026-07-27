# Cardholder Transaction Recency Baseline

## Executive finding

As of December 29, 2023, most recorded credit-card holders transacted recently:
the median recency was 10.0 days, and
24,625 customers (80.8%) had a
transaction within 30 days.

The 30-day scenario identifies a broad 5,835-customer population. A 60-day
scenario narrows this to 1,686 customers (5.5%), while a 90-day scenario leaves
457 (1.5%). These groups are better described as having elevated overall
transaction recency than as having inactive credit cards.

## Scenario results

| Scenario | Customers | Population | Prior 90d median (IQR) | Zero prior 90d | Full-period median | Active-month median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| More than 30 days | 5,835 | 19.2% | 4.0 (2.0–5.0) | 1.7% | 13.0 | 8.0 |
| More than 60 days | 1,686 | 5.5% | 4.0 (2.0–5.0) | 1.3% | 12.0 | 7.0 |
| More than 90 days | 457 | 1.5% | 4.0 (3.0–5.0) | 0.9% | 11.0 | 7.0 |

The groups beyond 60 and 90 days were historically light users: each had a
median of four transactions in the prior 90 days. The greater-than-60-day group
had a median of 12 full-period transactions across seven active months.
Nevertheless, 98.7% had at least one prior-period
transaction and 100.0% met the prior-history rule.

## Recent versus prior activity

Among 30,454 customers with sufficient history,
the median was 5.0 transactions in
both the prior and recent 90-day periods. Recent activity was lower for
43.8%, unchanged for
12.4%, and higher for
43.8%.

This balanced population-level result masks different individual trajectories.
Apparent lapse should therefore remain separate from a future decline analysis.

## Duplicate sensitivity

Collapsing exact customer/date/amount/type groups removed
55 excess rows for
28 eligible customers. It changed the
recent and prior windows by 18
and 11 rows, respectively.

The maximum change in any 30-, 60-, or 90-day scenario count was
0. Exact duplicate
interpretation does not affect the recency-threshold decision.

## Recommendation

Carry **more than 60 days** forward as the primary screening threshold, with
more than 90 days retained as a nested high-recency group.

The 60-day threshold is preferable for the next slice because:

- 30 days captures 19.2% of the population and is likely too broad for an
  initial review;
- 60 days produces a bounded 5.5% population with adequate observable history;
- nearly all customers beyond 60 days had some prior-period activity; and
- 90 days is a clearer lapse signal but identifies only 1.5% of customers.

This is a screening definition, not proof of card inactivity. Because the
selected customers were typically light users before their recent gap, the next
slice should examine activity trajectory before profiling customer
characteristics or proposing action.

## Limitations

- Transactions do not identify the card, account, or payment instrument used.
- Every eligible customer has at least 10 transactions, so the data omits a
  transaction-zero comparison group.
- The observation period is limited to 2023.
- First recorded transaction is only a proxy for observable history.
- This descriptive analysis does not explain why activity changed or whether
  outreach would improve it.
