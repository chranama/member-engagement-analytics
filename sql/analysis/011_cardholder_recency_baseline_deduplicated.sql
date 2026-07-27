WITH parameters AS (
    SELECT
        DATE '2023-12-29' AS analysis_date,
        DATE '2023-10-01' AS recent_90d_start,
        DATE '2023-07-03' AS prior_90d_start,
        DATE '2023-09-30' AS prior_90d_end
),
eligible_customers AS (
    SELECT customer_id
    FROM source.customers
    WHERE credit_card IS TRUE
),
transactions_collapsed AS (
    SELECT DISTINCT
        transactions.customer_id,
        transactions.transaction_date,
        transactions.amount_cop,
        transactions.transaction_type
    FROM source.transactions AS transactions
    INNER JOIN eligible_customers USING (customer_id)
),
customer_activity AS (
    SELECT
        transactions.customer_id,
        min(transactions.transaction_date) AS first_transaction_date,
        max(transactions.transaction_date) AS last_transaction_date,
        count(*) AS transactions_full_period,
        count(*) FILTER (
            WHERE transactions.transaction_date
                BETWEEN parameters.recent_90d_start
                    AND parameters.analysis_date
        ) AS transactions_recent_90d,
        count(*) FILTER (
            WHERE transactions.transaction_date
                BETWEEN parameters.prior_90d_start
                    AND parameters.prior_90d_end
        ) AS transactions_prior_90d,
        count(DISTINCT date_trunc('month', transactions.transaction_date))
            AS active_months
    FROM transactions_collapsed AS transactions
    CROSS JOIN parameters
    GROUP BY transactions.customer_id
)
SELECT
    customer_activity.customer_id,
    customer_activity.first_transaction_date,
    customer_activity.last_transaction_date,
    date_diff(
        'day',
        customer_activity.first_transaction_date,
        parameters.analysis_date
    ) + 1 AS observed_days,
    date_diff(
        'day',
        customer_activity.last_transaction_date,
        parameters.analysis_date
    ) AS recency_days,
    customer_activity.transactions_full_period,
    customer_activity.transactions_recent_90d,
    customer_activity.transactions_prior_90d,
    customer_activity.active_months,
    customer_activity.first_transaction_date
        <= parameters.prior_90d_start AS sufficient_prior_history
FROM customer_activity
CROSS JOIN parameters
ORDER BY customer_activity.customer_id;
