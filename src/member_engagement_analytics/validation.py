"""Inspect and validate the raw COFINFAD CSV files without persisting data."""

from __future__ import annotations

import csv
import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb

DATASET_NAME = "COFINFAD: Colombian Fintech Financial Analytics Dataset"
DATASET_VERSION = "1"
DATASET_SOURCE_URL = "https://data.mendeley.com/datasets/mhb4zn3258/1"
OBSERVATION_START = "2023-01-04"
OBSERVATION_END = "2023-12-29"

CUSTOMER_HEADERS = (
    "customer_id",
    "age",
    "gender",
    "location",
    "income_bracket",
    "occupation",
    "education_level",
    "marital_status",
    "household_size",
    "acquisition_channel",
    "customer_segment",
    "savings_account",
    "credit_card",
    "personal_loan",
    "investment_account",
    "insurance_product",
    "active_products",
    "app_logins_frequency",
    "feature_usage_diversity",
    "bill_payment_user",
    "auto_savings_enabled",
    "credit_utilization_ratio",
    "international_transactions",
    "failed_transactions",
    "tx_count",
    "avg_tx_value",
    "total_tx_volume",
    "first_tx",
    "last_tx",
    "base_satisfaction",
    "tx_satisfaction",
    "product_satisfaction",
    "satisfaction_score",
    "nps_score",
    "last_survey_date",
    "support_tickets_count",
    "resolved_tickets_ratio",
    "app_store_rating",
    "feedback_sentiment",
    "feature_requests",
    "complaint_topics",
    "clv_segment",
    "monthly_transaction_count",
    "average_transaction_value",
    "total_transaction_volume",
    "transaction_frequency",
    "last_transaction_date",
    "preferred_transaction_type",
    "first_transaction_date",
    "weekend_transaction_ratio",
    "avg_daily_transactions",
    "customer_tenure",
    "churn_probability",
    "customer_lifetime_value",
)

TRANSACTION_HEADERS = ("customer_id", "date", "amount", "type")

BOOLEAN_COLUMNS = (
    "savings_account",
    "credit_card",
    "personal_loan",
    "investment_account",
    "insurance_product",
    "bill_payment_user",
    "auto_savings_enabled",
)

CUSTOMER_INTEGER_COLUMNS = (
    "age",
    "household_size",
    "active_products",
    "app_logins_frequency",
    "feature_usage_diversity",
    "international_transactions",
    "failed_transactions",
    "tx_count",
    "satisfaction_score",
    "nps_score",
    "support_tickets_count",
)

CUSTOMER_DATE_COLUMNS = (
    "first_tx",
    "last_tx",
    "last_survey_date",
    "last_transaction_date",
    "first_transaction_date",
)

CUSTOMER_DECIMAL_COLUMNS = ("total_tx_volume", "total_transaction_volume")

CUSTOMER_DOUBLE_COLUMNS = (
    "credit_utilization_ratio",
    "avg_tx_value",
    "base_satisfaction",
    "tx_satisfaction",
    "product_satisfaction",
    "resolved_tickets_ratio",
    "app_store_rating",
    "monthly_transaction_count",
    "average_transaction_value",
    "transaction_frequency",
    "weekend_transaction_ratio",
    "avg_daily_transactions",
    "customer_tenure",
    "churn_probability",
    "customer_lifetime_value",
)


def _customer_types() -> dict[str, str]:
    types = dict.fromkeys(CUSTOMER_HEADERS, "VARCHAR")
    types["customer_id"] = "BIGINT"
    types.update(dict.fromkeys(CUSTOMER_INTEGER_COLUMNS, "INTEGER"))
    types.update(dict.fromkeys(BOOLEAN_COLUMNS, "BOOLEAN"))
    types.update(dict.fromkeys(CUSTOMER_DATE_COLUMNS, "DATE"))
    types.update(dict.fromkeys(CUSTOMER_DECIMAL_COLUMNS, "DECIMAL(20,2)"))
    types.update(dict.fromkeys(CUSTOMER_DOUBLE_COLUMNS, "DOUBLE"))
    return types


CUSTOMER_TYPES = _customer_types()
TRANSACTION_TYPES = {
    "customer_id": "BIGINT",
    "date": "DATE",
    "amount": "DECIMAL(20,2)",
    "type": "VARCHAR",
}

CUSTOMER_CATEGORY_ALLOWLISTS: Mapping[str, tuple[str, ...]] = {
    "gender": ("Female", "Male", "Other"),
    "income_bracket": ("High", "Low", "Medium", "Very High"),
    "education_level": ("Bachelor", "High School", "Master", "PhD"),
    "marital_status": ("Divorced", "Married", "Single", "Widowed"),
    "acquisition_channel": ("Organic", "Paid Ad", "Partnership", "Referral"),
    "customer_segment": ("inactive", "occasional", "power", "regular"),
    "feedback_sentiment": ("Negative", "Neutral", "Positive"),
    "clv_segment": ("Bronze", "Gold", "Platinum", "Silver"),
    "preferred_transaction_type": (
        "Deposit",
        "Payment",
        "Transfer",
        "Withdrawal",
    ),
    **{column: ("False", "True") for column in BOOLEAN_COLUMNS},
}

TRANSACTION_CATEGORY_ALLOWLISTS: Mapping[str, tuple[str, ...]] = {
    "type": ("Deposit", "Payment", "Transfer", "Withdrawal"),
}


class CheckStatus(StrEnum):
    """Outcome of one preflight check."""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


@dataclass(frozen=True)
class SourceFileSpec:
    """Expected identity and structure of one raw source file."""

    key: str
    filename: str
    expected_bytes: int
    expected_sha256: str
    expected_rows: int
    headers: tuple[str, ...]
    expected_types: Mapping[str, str]


@dataclass(frozen=True)
class CheckResult:
    """Aggregate result of one inspection or validation check."""

    code: str
    status: CheckStatus
    message: str
    metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class PreflightReport:
    """Deterministic aggregate report for a pair of raw COFINFAD files."""

    raw_directory: str
    dataset_name: str = DATASET_NAME
    dataset_version: str = DATASET_VERSION
    dataset_source_url: str = DATASET_SOURCE_URL
    report_schema_version: int = 1
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        """Return whether any blocking check failed."""

        return any(check.status is CheckStatus.FAIL for check in self.checks)

    def status_counts(self) -> dict[str, int]:
        """Count checks by status."""

        counts = {status.value: 0 for status in CheckStatus}
        for check in self.checks:
            counts[check.status.value] += 1
        return counts


SOURCE_SPECS = (
    SourceFileSpec(
        key="customers",
        filename="customer_data.csv",
        expected_bytes=23_519_477,
        expected_sha256=(
            "bb3865b68c247caaa28821238c3d9fa9c745ca8837c42f89b5d7e310beb98c8d"
        ),
        expected_rows=48_723,
        headers=CUSTOMER_HEADERS,
        expected_types=CUSTOMER_TYPES,
    ),
    SourceFileSpec(
        key="transactions",
        filename="transactions_data.csv",
        expected_bytes=119_006_497,
        expected_sha256=(
            "09fa21b8d74692cbfbf10ee58b55b00c874280a57aa926650e2fe55c60859ec6"
        ),
        expected_rows=3_159_157,
        headers=TRANSACTION_HEADERS,
        expected_types=TRANSACTION_TYPES,
    ),
)


def _check(
    code: str,
    status: CheckStatus,
    message: str,
    **metrics: Any,
) -> CheckResult:
    return CheckResult(code=code, status=status, message=message, metrics=metrics)


def _quoted_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _sql_string(value: str) -> str:
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _scan_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    newline_count = 0
    final_byte = b""

    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            newline_count += chunk.count(b"\n")
            final_byte = chunk[-1:]

    physical_lines = newline_count + int(bool(final_byte) and final_byte != b"\n")
    return max(physical_lines - 1, 0), digest.hexdigest()


def _read_header(path: Path) -> tuple[str, ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        return tuple(next(reader))


def inspect_file_metadata(
    raw_directory: Path,
    spec: SourceFileSpec,
) -> tuple[dict[str, Any], list[CheckResult]]:
    """Inspect file identity without altering the source file."""

    path = raw_directory / spec.filename
    profile: dict[str, Any] = {"filename": spec.filename, "present": path.is_file()}
    checks: list[CheckResult] = []

    if not path.is_file():
        checks.append(
            _check(
                f"{spec.key}.file_exists",
                CheckStatus.FAIL,
                f"Required source file {spec.filename} is missing.",
            )
        )
        return profile, checks

    readable = os.access(path, os.R_OK)
    checks.append(
        _check(
            f"{spec.key}.file_readable",
            CheckStatus.PASS if readable else CheckStatus.FAIL,
            (
                f"{spec.filename} is readable."
                if readable
                else f"{spec.filename} is not readable."
            ),
        )
    )
    if not readable:
        return profile, checks

    profile["byte_size"] = path.stat().st_size

    try:
        row_count, sha256 = _scan_file(path)
        headers = _read_header(path)
    except (OSError, UnicodeError, csv.Error, StopIteration) as error:
        checks.append(
            _check(
                f"{spec.key}.file_structure",
                CheckStatus.FAIL,
                f"{spec.filename} could not be read as a UTF-8 CSV.",
                error_type=type(error).__name__,
            )
        )
        return profile, checks

    profile.update(
        {
            "data_rows": row_count,
            "sha256": sha256,
            "headers": list(headers),
        }
    )

    checks.extend(
        [
            _check(
                f"{spec.key}.byte_size",
                (
                    CheckStatus.PASS
                    if profile["byte_size"] == spec.expected_bytes
                    else CheckStatus.FAIL
                ),
                (
                    f"{spec.filename} has the expected byte size."
                    if profile["byte_size"] == spec.expected_bytes
                    else f"{spec.filename} has an unexpected byte size."
                ),
                expected=spec.expected_bytes,
                observed=profile["byte_size"],
            ),
            _check(
                f"{spec.key}.checksum",
                (
                    CheckStatus.PASS
                    if sha256 == spec.expected_sha256
                    else CheckStatus.FAIL
                ),
                (
                    f"{spec.filename} matches the expected SHA-256 checksum."
                    if sha256 == spec.expected_sha256
                    else f"{spec.filename} does not match the expected checksum."
                ),
                expected=spec.expected_sha256,
                observed=sha256,
            ),
            _check(
                f"{spec.key}.header",
                CheckStatus.PASS if headers == spec.headers else CheckStatus.FAIL,
                (
                    f"{spec.filename} has the expected columns in the expected order."
                    if headers == spec.headers
                    else f"{spec.filename} has an unexpected header."
                ),
                expected_columns=len(spec.headers),
                observed_columns=len(headers),
                missing_columns=sorted(set(spec.headers) - set(headers)),
                unexpected_columns=sorted(set(headers) - set(spec.headers)),
                order_matches=headers == spec.headers,
            ),
            _check(
                f"{spec.key}.row_count",
                (
                    CheckStatus.PASS
                    if row_count == spec.expected_rows
                    else CheckStatus.FAIL
                ),
                (
                    f"{spec.filename} has the expected number of data rows."
                    if row_count == spec.expected_rows
                    else f"{spec.filename} has an unexpected number of data rows."
                ),
                expected=spec.expected_rows,
                observed=row_count,
            ),
        ]
    )
    return profile, checks


def _fetch_one(
    connection: duckdb.DuckDBPyConnection,
    query: str,
) -> dict[str, Any]:
    cursor = connection.execute(query)
    row = cursor.fetchone()
    if row is None:
        return {}
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row, strict=True))


def _create_source_views(
    connection: duckdb.DuckDBPyConnection,
    raw_directory: Path,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in SOURCE_SPECS:
        path_literal = _sql_string(str((raw_directory / spec.filename).resolve()))
        relation = _quoted_identifier(spec.key)
        connection.execute(
            f"""
            CREATE TEMP VIEW {relation} AS
            SELECT *
            FROM read_csv(
                {path_literal},
                header = true,
                all_varchar = true,
                nullstr = ''
            )
            """
        )
        counts[spec.key] = connection.execute(
            f"SELECT count(*) FROM {relation}"
        ).fetchone()[0]
    return counts


def _null_counts(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    columns: tuple[str, ...],
) -> dict[str, int]:
    expressions = []
    for column in columns:
        identifier = _quoted_identifier(column)
        expressions.append(
            f"count_if(nullif(trim({identifier}), '') IS NULL) "
            f"AS {_quoted_identifier(column)}"
        )
    return {
        key: int(value)
        for key, value in _fetch_one(
            connection,
            f"SELECT {', '.join(expressions)} FROM {_quoted_identifier(relation)}",
        ).items()
    }


def _parse_failures(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    expected_types: Mapping[str, str],
) -> dict[str, int]:
    typed_columns = {
        column: data_type
        for column, data_type in expected_types.items()
        if data_type != "VARCHAR"
    }
    expressions = []
    for column, data_type in typed_columns.items():
        identifier = _quoted_identifier(column)
        expressions.append(
            "count_if("
            f"nullif(trim({identifier}), '') IS NOT NULL "
            f"AND try_cast(trim({identifier}) AS {data_type}) IS NULL"
            f") AS {_quoted_identifier(column)}"
        )
    return {
        key: int(value)
        for key, value in _fetch_one(
            connection,
            f"SELECT {', '.join(expressions)} FROM {_quoted_identifier(relation)}",
        ).items()
    }


def _category_profile(
    connection: duckdb.DuckDBPyConnection,
    relation: str,
    allowlists: Mapping[str, tuple[str, ...]],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    observed: dict[str, dict[str, int]] = {}
    unexpected: dict[str, dict[str, int]] = {}

    for column, allowed_values in allowlists.items():
        identifier = _quoted_identifier(column)
        rows = connection.execute(
            f"""
            SELECT
                coalesce(nullif(trim({identifier}), ''), '<NULL>') AS value,
                count(*) AS rows
            FROM {_quoted_identifier(relation)}
            GROUP BY value
            ORDER BY value
            """
        ).fetchall()
        frequencies = {str(value): int(count) for value, count in rows}
        observed[column] = frequencies
        allowed = set(allowed_values) | {"<NULL>"}
        unknown = {
            value: count for value, count in frequencies.items() if value not in allowed
        }
        if unknown:
            unexpected[column] = unknown

    return observed, unexpected


def profile_customers(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, Any], list[CheckResult]]:
    """Profile customer rows and return aggregate checks."""

    checks: list[CheckResult] = []
    null_counts = _null_counts(connection, "customers", CUSTOMER_HEADERS)
    parse_failures = _parse_failures(connection, "customers", CUSTOMER_TYPES)
    categories, unexpected_categories = _category_profile(
        connection,
        "customers",
        CUSTOMER_CATEGORY_ALLOWLISTS,
    )

    key_metrics = _fetch_one(
        connection,
        """
        SELECT
            count(*) AS rows,
            count_if(nullif(trim(customer_id), '') IS NULL) AS null_ids,
            count(DISTINCT try_cast(customer_id AS BIGINT)) AS distinct_ids
        FROM customers
        """,
    )
    duplicate_rows = (
        int(key_metrics["rows"])
        - int(key_metrics["null_ids"])
        - int(key_metrics["distinct_ids"])
    )
    key_metrics["duplicate_id_rows"] = duplicate_rows

    type_failure_total = sum(parse_failures.values())
    checks.append(
        _check(
            "customers.schema_parseability",
            CheckStatus.PASS if type_failure_total == 0 else CheckStatus.FAIL,
            (
                "All non-empty customer values parse to their expected types."
                if type_failure_total == 0
                else "Some customer values do not parse to their expected types."
            ),
            failed_values=type_failure_total,
            affected_columns=sum(value > 0 for value in parse_failures.values()),
        )
    )

    customer_key_valid = int(key_metrics["null_ids"]) == 0 and int(duplicate_rows) == 0
    checks.append(
        _check(
            "customers.primary_key",
            CheckStatus.PASS if customer_key_valid else CheckStatus.FAIL,
            (
                "Customer identifiers are non-null and unique."
                if customer_key_valid
                else "Customer identifiers contain null or duplicate values."
            ),
            null_ids=int(key_metrics["null_ids"]),
            duplicate_id_rows=duplicate_rows,
            distinct_ids=int(key_metrics["distinct_ids"]),
        )
    )

    checks.append(
        _check(
            "customers.categories",
            (CheckStatus.PASS if not unexpected_categories else CheckStatus.WARNING),
            (
                "Contracted customer categories contain only expected values."
                if not unexpected_categories
                else "Some contracted customer categories contain unexpected values."
            ),
            unexpected_values=unexpected_categories,
        )
    )

    range_metrics = _fetch_one(
        connection,
        """
        SELECT
            count_if(try_cast(age AS INTEGER) NOT BETWEEN 18 AND 100)
                AS age_outside_expected_range,
            count_if(try_cast(household_size AS INTEGER) < 1)
                AS household_size_below_one,
            count_if(try_cast(active_products AS INTEGER) < 0)
                AS negative_active_products,
            count_if(try_cast(app_logins_frequency AS INTEGER) < 0)
                AS negative_app_logins,
            count_if(try_cast(failed_transactions AS INTEGER) < 0)
                AS negative_failed_transactions,
            count_if(try_cast(credit_utilization_ratio AS DOUBLE)
                NOT BETWEEN 0 AND 1)
                AS credit_utilization_outside_zero_one,
            count_if(try_cast(resolved_tickets_ratio AS DOUBLE)
                NOT BETWEEN 0 AND 1)
                AS resolved_ticket_ratio_outside_zero_one,
            count_if(try_cast(weekend_transaction_ratio AS DOUBLE)
                NOT BETWEEN 0 AND 1)
                AS weekend_ratio_outside_zero_one,
            count_if(try_cast(churn_probability AS DOUBLE)
                NOT BETWEEN 0 AND 1)
                AS churn_probability_outside_zero_one,
            count_if(try_cast(nps_score AS INTEGER) NOT BETWEEN -100 AND 100)
                AS nps_outside_expected_range,
            count_if(try_cast(app_store_rating AS DOUBLE) NOT BETWEEN 1 AND 5)
                AS app_rating_outside_expected_range
        FROM customers
        """,
    )
    range_violations = sum(int(value) for value in range_metrics.values())
    checks.append(
        _check(
            "customers.business_ranges",
            CheckStatus.PASS if range_violations == 0 else CheckStatus.WARNING,
            (
                "Customer values fall within the inspected business ranges."
                if range_violations == 0
                else "Some customer values fall outside inspected business ranges."
            ),
            **{key: int(value) for key, value in range_metrics.items()},
        )
    )

    credit_card_frequencies = categories["credit_card"]
    profile = {
        "row_count": int(key_metrics["rows"]),
        "expected_types": dict(CUSTOMER_TYPES),
        "null_counts": null_counts,
        "parse_failures": parse_failures,
        "category_frequencies": categories,
        "credit_card_holders": int(credit_card_frequencies.get("True", 0)),
        "key_metrics": {key: int(value) for key, value in key_metrics.items()},
        "business_range_violations": {
            key: int(value) for key, value in range_metrics.items()
        },
    }
    return profile, checks


def profile_transactions(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, Any], list[CheckResult]]:
    """Profile transaction rows and return aggregate checks."""

    checks: list[CheckResult] = []
    null_counts = _null_counts(connection, "transactions", TRANSACTION_HEADERS)
    parse_failures = _parse_failures(
        connection,
        "transactions",
        TRANSACTION_TYPES,
    )
    categories, unexpected_categories = _category_profile(
        connection,
        "transactions",
        TRANSACTION_CATEGORY_ALLOWLISTS,
    )

    type_failure_total = sum(parse_failures.values())
    checks.append(
        _check(
            "transactions.schema_parseability",
            CheckStatus.PASS if type_failure_total == 0 else CheckStatus.FAIL,
            (
                "All non-empty transaction values parse to their expected types."
                if type_failure_total == 0
                else "Some transaction values do not parse to their expected types."
            ),
            failed_values=type_failure_total,
            affected_columns=sum(value > 0 for value in parse_failures.values()),
        )
    )

    required_nulls = sum(null_counts.values())
    checks.append(
        _check(
            "transactions.required_values",
            CheckStatus.PASS if required_nulls == 0 else CheckStatus.FAIL,
            (
                "All required transaction values are present."
                if required_nulls == 0
                else "Some required transaction values are null or empty."
            ),
            null_or_empty_values=required_nulls,
            by_column=null_counts,
        )
    )

    checks.append(
        _check(
            "transactions.categories",
            (CheckStatus.PASS if not unexpected_categories else CheckStatus.WARNING),
            (
                "Transaction types contain only expected values."
                if not unexpected_categories
                else "Transaction types contain unexpected values."
            ),
            unexpected_values=unexpected_categories,
        )
    )

    transaction_metrics = _fetch_one(
        connection,
        f"""
        SELECT
            count(*) AS rows,
            min(try_cast(date AS DATE)) AS minimum_date,
            max(try_cast(date AS DATE)) AS maximum_date,
            min(try_cast(amount AS DECIMAL(20,2))) AS minimum_amount_cop,
            max(try_cast(amount AS DECIMAL(20,2))) AS maximum_amount_cop,
            sum(try_cast(amount AS DECIMAL(20,2))) AS total_amount_cop,
            count_if(try_cast(amount AS DECIMAL(20,2)) = 0) AS zero_amount_rows,
            count_if(try_cast(amount AS DECIMAL(20,2)) < 0)
                AS negative_amount_rows,
            count_if(try_cast(date AS DATE)
                NOT BETWEEN DATE '{OBSERVATION_START}' AND DATE '{OBSERVATION_END}')
                AS dates_outside_observation_window
        FROM transactions
        """,
    )

    amount_or_date_warnings = (
        int(transaction_metrics["zero_amount_rows"])
        + int(transaction_metrics["negative_amount_rows"])
        + int(transaction_metrics["dates_outside_observation_window"])
    )
    checks.append(
        _check(
            "transactions.ranges",
            (CheckStatus.PASS if amount_or_date_warnings == 0 else CheckStatus.WARNING),
            (
                "Transaction dates and amounts satisfy the inspected ranges."
                if amount_or_date_warnings == 0
                else "Some transaction dates or amounts warrant review."
            ),
            zero_amount_rows=int(transaction_metrics["zero_amount_rows"]),
            negative_amount_rows=int(transaction_metrics["negative_amount_rows"]),
            dates_outside_observation_window=int(
                transaction_metrics["dates_outside_observation_window"]
            ),
        )
    )

    duplicate_metrics = _fetch_one(
        connection,
        """
        SELECT
            count(*) AS duplicate_groups,
            coalesce(sum(rows_in_group - 1), 0) AS excess_duplicate_rows
        FROM (
            SELECT customer_id, date, amount, type, count(*) AS rows_in_group
            FROM transactions
            GROUP BY customer_id, date, amount, type
            HAVING count(*) > 1
        )
        """,
    )
    duplicate_rows = int(duplicate_metrics["excess_duplicate_rows"])
    checks.append(
        _check(
            "transactions.duplicate_looking_rows",
            CheckStatus.PASS if duplicate_rows == 0 else CheckStatus.WARNING,
            (
                "No exact duplicate-looking transaction rows were found."
                if duplicate_rows == 0
                else "Exact duplicate-looking transaction rows require interpretation."
            ),
            duplicate_groups=int(duplicate_metrics["duplicate_groups"]),
            excess_duplicate_rows=duplicate_rows,
        )
    )

    profile = {
        "row_count": int(transaction_metrics["rows"]),
        "expected_types": dict(TRANSACTION_TYPES),
        "null_counts": null_counts,
        "parse_failures": parse_failures,
        "category_frequencies": categories,
        "date_range": {
            "minimum": transaction_metrics["minimum_date"],
            "maximum": transaction_metrics["maximum_date"],
        },
        "amount_cop": {
            "minimum": transaction_metrics["minimum_amount_cop"],
            "maximum": transaction_metrics["maximum_amount_cop"],
            "total": transaction_metrics["total_amount_cop"],
            "zero_rows": int(transaction_metrics["zero_amount_rows"]),
            "negative_rows": int(transaction_metrics["negative_amount_rows"]),
        },
        "duplicate_looking_rows": {
            key: int(value) for key, value in duplicate_metrics.items()
        },
    }
    return profile, checks


def check_relationships(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, Any], list[CheckResult]]:
    """Inspect cross-file relationships without emitting identifiers."""

    checks: list[CheckResult] = []
    relationship_metrics = _fetch_one(
        connection,
        """
        SELECT
            count(*) AS orphan_transaction_rows,
            count(DISTINCT try_cast(t.customer_id AS BIGINT))
                AS orphan_customer_ids
        FROM transactions AS t
        LEFT JOIN customers AS c
            ON try_cast(t.customer_id AS BIGINT)
                = try_cast(c.customer_id AS BIGINT)
        WHERE c.customer_id IS NULL
        """,
    )
    orphan_rows = int(relationship_metrics["orphan_transaction_rows"])
    checks.append(
        _check(
            "relationships.transaction_customers",
            CheckStatus.PASS if orphan_rows == 0 else CheckStatus.FAIL,
            (
                "Every transaction resolves to a customer."
                if orphan_rows == 0
                else "Some transactions do not resolve to a customer."
            ),
            orphan_transaction_rows=orphan_rows,
            orphan_customer_ids=int(relationship_metrics["orphan_customer_ids"]),
        )
    )

    customers_without_transactions = connection.execute(
        """
        SELECT count(*)
        FROM customers AS c
        LEFT JOIN transactions AS t
            ON try_cast(c.customer_id AS BIGINT)
                = try_cast(t.customer_id AS BIGINT)
        WHERE t.customer_id IS NULL
        """
    ).fetchone()[0]
    checks.append(
        _check(
            "relationships.customers_without_transactions",
            (
                CheckStatus.PASS
                if customers_without_transactions == 0
                else CheckStatus.WARNING
            ),
            (
                "Every customer has at least one transaction row."
                if customers_without_transactions == 0
                else "Some customers have no transaction rows."
            ),
            customers_without_transactions=int(customers_without_transactions),
        )
    )

    distribution = _fetch_one(
        connection,
        """
        WITH transaction_counts AS (
            SELECT
                try_cast(customer_id AS BIGINT) AS customer_id,
                count(*) AS transaction_count
            FROM transactions
            GROUP BY customer_id
        )
        SELECT
            count(*) AS customers_with_transactions,
            min(transaction_count) AS minimum,
            round(avg(transaction_count), 4) AS mean,
            round(median(transaction_count), 2) AS median,
            round(quantile_cont(transaction_count, 0.95), 2) AS p95,
            max(transaction_count) AS maximum
        FROM transaction_counts
        """,
    )
    p95 = float(distribution["p95"])
    maximum_to_p95_ratio = (
        round(float(distribution["maximum"]) / p95, 2) if p95 else None
    )
    concentrated = maximum_to_p95_ratio is not None and maximum_to_p95_ratio > 10
    checks.append(
        _check(
            "relationships.transaction_count_concentration",
            CheckStatus.WARNING if concentrated else CheckStatus.PASS,
            (
                "The transaction-count distribution contains an extreme upper tail."
                if concentrated
                else "The transaction-count distribution has no extreme upper tail."
            ),
            maximum=int(distribution["maximum"]),
            p95=p95,
            maximum_to_p95_ratio=maximum_to_p95_ratio,
            review_threshold_ratio=10,
        )
    )

    profile = {
        "orphan_transaction_rows": orphan_rows,
        "orphan_customer_ids": int(relationship_metrics["orphan_customer_ids"]),
        "customers_without_transactions": int(customers_without_transactions),
        "transactions_per_customer": {
            **distribution,
            "maximum_to_p95_ratio": maximum_to_p95_ratio,
        },
    }
    return profile, checks


def compare_publisher_aggregates(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[dict[str, Any], CheckResult]:
    """Compare ledger aggregates with publisher-derived customer columns."""

    comparisons = _fetch_one(
        connection,
        """
        WITH ledger AS (
            SELECT
                try_cast(customer_id AS BIGINT) AS customer_id,
                count(*) AS ledger_tx_count,
                sum(try_cast(amount AS DECIMAL(20,2))) AS ledger_total_volume,
                min(try_cast(date AS DATE)) AS ledger_first_date,
                max(try_cast(date AS DATE)) AS ledger_last_date
            FROM transactions
            GROUP BY customer_id
        )
        SELECT
            count(*) AS customers_compared,
            count_if(
                try_cast(c.tx_count AS BIGINT)
                    IS DISTINCT FROM ledger.ledger_tx_count
            ) AS tx_count_mismatches,
            count_if(
                try_cast(c.total_tx_volume AS DECIMAL(20,2))
                    IS DISTINCT FROM ledger.ledger_total_volume
            ) AS total_tx_volume_mismatches,
            count_if(
                try_cast(c.total_transaction_volume AS DECIMAL(20,2))
                    IS DISTINCT FROM ledger.ledger_total_volume
            ) AS total_transaction_volume_mismatches,
            count_if(
                try_cast(c.first_tx AS DATE)
                    IS DISTINCT FROM ledger.ledger_first_date
            ) AS first_tx_mismatches,
            count_if(
                try_cast(c.first_transaction_date AS DATE)
                    IS DISTINCT FROM ledger.ledger_first_date
            ) AS first_transaction_date_mismatches,
            count_if(
                try_cast(c.last_tx AS DATE)
                    IS DISTINCT FROM ledger.ledger_last_date
            ) AS last_tx_mismatches,
            count_if(
                try_cast(c.last_transaction_date AS DATE)
                    IS DISTINCT FROM ledger.ledger_last_date
            ) AS last_transaction_date_mismatches
        FROM customers AS c
        INNER JOIN ledger
            ON try_cast(c.customer_id AS BIGINT) = ledger.customer_id
        """,
    )
    mismatch_counts = {
        key: int(value)
        for key, value in comparisons.items()
        if key != "customers_compared"
    }
    total_mismatches = sum(mismatch_counts.values())
    check = _check(
        "relationships.publisher_derived_aggregates",
        CheckStatus.PASS if total_mismatches == 0 else CheckStatus.WARNING,
        (
            "Publisher-derived customer aggregates match the transaction ledger."
            if total_mismatches == 0
            else "Publisher-derived customer fields differ from ledger aggregates."
        ),
        customers_compared=int(comparisons["customers_compared"]),
        **mismatch_counts,
    )
    return (
        {
            "customers_compared": int(comparisons["customers_compared"]),
            **mismatch_counts,
        },
        check,
    )


def inspect_raw_sources(
    raw_directory: Path,
    *,
    display_directory: str | None = None,
) -> PreflightReport:
    """Run the complete read-only preflight over raw CSV files."""

    report = PreflightReport(
        raw_directory=display_directory or raw_directory.as_posix()
    )

    for spec in SOURCE_SPECS:
        profile, checks = inspect_file_metadata(raw_directory, spec)
        report.files[spec.key] = profile
        report.checks.extend(checks)

    if report.has_failures:
        return report

    connection = duckdb.connect(database=":memory:")
    try:
        try:
            loaded_counts = _create_source_views(connection, raw_directory)
        except duckdb.Error as error:
            report.checks.append(
                _check(
                    "files.csv_readability",
                    CheckStatus.FAIL,
                    "DuckDB could not complete a strict scan of the raw CSV files.",
                    error_type=type(error).__name__,
                )
            )
            return report

        expected_counts = {spec.key: spec.expected_rows for spec in SOURCE_SPECS}
        counts_match = loaded_counts == expected_counts
        report.checks.append(
            _check(
                "files.csv_readability",
                CheckStatus.PASS if counts_match else CheckStatus.FAIL,
                (
                    "DuckDB scanned both raw CSV files without persisting data."
                    if counts_match
                    else "DuckDB scan counts do not match the source contract."
                ),
                expected_rows=expected_counts,
                scanned_rows=loaded_counts,
            )
        )
        if not counts_match:
            return report

        customer_profile, customer_checks = profile_customers(connection)
        transaction_profile, transaction_checks = profile_transactions(connection)
        relationship_profile, relationship_checks = check_relationships(connection)
        aggregate_profile, aggregate_check = compare_publisher_aggregates(connection)

        relationship_profile["publisher_aggregate_comparison"] = aggregate_profile
        report.profiles.update(
            {
                "customers": customer_profile,
                "transactions": transaction_profile,
                "relationships": relationship_profile,
            }
        )
        report.checks.extend(customer_checks)
        report.checks.extend(transaction_checks)
        report.checks.extend(relationship_checks)
        report.checks.append(aggregate_check)
        return report
    finally:
        connection.close()
