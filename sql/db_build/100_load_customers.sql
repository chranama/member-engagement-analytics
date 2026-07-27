INSERT INTO source.customers
SELECT
    CAST(NULLIF(trim(customer_id), '') AS BIGINT) AS customer_id,
    CAST(NULLIF(trim(age), '') AS INTEGER) AS age,
    NULLIF(trim(gender), '') AS gender,
    NULLIF(trim(location), '') AS location,
    NULLIF(trim(income_bracket), '') AS income_bracket,
    NULLIF(trim(occupation), '') AS occupation,
    NULLIF(trim(education_level), '') AS education_level,
    NULLIF(trim(marital_status), '') AS marital_status,
    CAST(NULLIF(trim(household_size), '') AS INTEGER) AS household_size,
    NULLIF(trim(acquisition_channel), '') AS acquisition_channel,
    NULLIF(trim(customer_segment), '') AS customer_segment,
    CAST(NULLIF(trim(savings_account), '') AS BOOLEAN) AS savings_account,
    CAST(NULLIF(trim(credit_card), '') AS BOOLEAN) AS credit_card,
    CAST(NULLIF(trim(personal_loan), '') AS BOOLEAN) AS personal_loan,
    CAST(NULLIF(trim(investment_account), '') AS BOOLEAN) AS investment_account,
    CAST(NULLIF(trim(insurance_product), '') AS BOOLEAN) AS insurance_product,
    CAST(NULLIF(trim(active_products), '') AS INTEGER) AS active_products,
    CAST(NULLIF(trim(app_logins_frequency), '') AS INTEGER)
        AS app_logins_frequency,
    CAST(NULLIF(trim(feature_usage_diversity), '') AS INTEGER)
        AS feature_usage_diversity,
    CAST(NULLIF(trim(bill_payment_user), '') AS BOOLEAN) AS bill_payment_user,
    CAST(NULLIF(trim(auto_savings_enabled), '') AS BOOLEAN)
        AS auto_savings_enabled,
    CAST(NULLIF(trim(credit_utilization_ratio), '') AS DOUBLE)
        AS credit_utilization_ratio,
    CAST(NULLIF(trim(international_transactions), '') AS INTEGER)
        AS international_transactions,
    CAST(NULLIF(trim(failed_transactions), '') AS INTEGER)
        AS failed_transactions,
    CAST(NULLIF(trim(tx_count), '') AS INTEGER) AS tx_count,
    CAST(NULLIF(trim(avg_tx_value), '') AS DOUBLE) AS avg_tx_value,
    CAST(NULLIF(trim(total_tx_volume), '') AS DECIMAL(20,2))
        AS total_tx_volume,
    CAST(NULLIF(trim(first_tx), '') AS DATE) AS first_tx,
    CAST(NULLIF(trim(last_tx), '') AS DATE) AS last_tx,
    CAST(NULLIF(trim(base_satisfaction), '') AS DOUBLE) AS base_satisfaction,
    CAST(NULLIF(trim(tx_satisfaction), '') AS DOUBLE) AS tx_satisfaction,
    CAST(NULLIF(trim(product_satisfaction), '') AS DOUBLE)
        AS product_satisfaction,
    CAST(NULLIF(trim(satisfaction_score), '') AS INTEGER)
        AS satisfaction_score,
    CAST(NULLIF(trim(nps_score), '') AS INTEGER) AS nps_score,
    CAST(NULLIF(trim(last_survey_date), '') AS DATE) AS last_survey_date,
    CAST(NULLIF(trim(support_tickets_count), '') AS INTEGER)
        AS support_tickets_count,
    CAST(NULLIF(trim(resolved_tickets_ratio), '') AS DOUBLE)
        AS resolved_tickets_ratio,
    CAST(NULLIF(trim(app_store_rating), '') AS DOUBLE) AS app_store_rating,
    NULLIF(trim(feedback_sentiment), '') AS feedback_sentiment,
    NULLIF(trim(feature_requests), '') AS feature_requests,
    NULLIF(trim(complaint_topics), '') AS complaint_topics,
    NULLIF(trim(clv_segment), '') AS clv_segment,
    CAST(NULLIF(trim(monthly_transaction_count), '') AS DOUBLE)
        AS monthly_transaction_count,
    CAST(NULLIF(trim(average_transaction_value), '') AS DOUBLE)
        AS average_transaction_value,
    CAST(NULLIF(trim(total_transaction_volume), '') AS DECIMAL(20,2))
        AS total_transaction_volume,
    CAST(NULLIF(trim(transaction_frequency), '') AS DOUBLE)
        AS transaction_frequency,
    CAST(NULLIF(trim(last_transaction_date), '') AS DATE)
        AS last_transaction_date,
    NULLIF(trim(preferred_transaction_type), '')
        AS preferred_transaction_type,
    CAST(NULLIF(trim(first_transaction_date), '') AS DATE)
        AS first_transaction_date,
    CAST(NULLIF(trim(weekend_transaction_ratio), '') AS DOUBLE)
        AS weekend_transaction_ratio,
    CAST(NULLIF(trim(avg_daily_transactions), '') AS DOUBLE)
        AS avg_daily_transactions,
    CAST(NULLIF(trim(customer_tenure), '') AS DOUBLE) AS customer_tenure,
    CAST(NULLIF(trim(churn_probability), '') AS DOUBLE) AS churn_probability,
    CAST(NULLIF(trim(customer_lifetime_value), '') AS DOUBLE)
        AS customer_lifetime_value
FROM read_csv(
    ?,
    header = true,
    all_varchar = true,
    nullstr = ''
);
