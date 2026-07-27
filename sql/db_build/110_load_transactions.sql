INSERT INTO source.transactions
WITH typed AS (
    SELECT
        CAST(NULLIF(trim(customer_id), '') AS BIGINT) AS customer_id,
        CAST(NULLIF(trim("date"), '') AS DATE) AS transaction_date,
        CAST(NULLIF(trim(amount), '') AS DECIMAL(20,2)) AS amount_cop,
        NULLIF(trim("type"), '') AS transaction_type
    FROM read_csv(
        ?,
        header = true,
        all_varchar = true,
        nullstr = ''
    )
),
flagged AS (
    SELECT
        *,
        count(*) OVER (
            PARTITION BY
                customer_id,
                transaction_date,
                amount_cop,
                transaction_type
        ) > 1 AS is_duplicate_looking
    FROM typed
)
SELECT
    row_number() OVER () AS transaction_row_id,
    customer_id,
    transaction_date,
    amount_cop,
    transaction_type,
    is_duplicate_looking
FROM flagged;
