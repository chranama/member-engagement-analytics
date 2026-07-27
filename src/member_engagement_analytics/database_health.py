"""Inspect the health of the existing analytical DuckDB artifact."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from member_engagement_analytics.database import (
    DEFAULT_DATABASE_PATH,
    REPOSITORY_ROOT,
    database_structure_checks,
    open_database,
)
from member_engagement_analytics.raw_data_validation import CheckResult, CheckStatus


@dataclass
class DatabaseHealthReport:
    """Aggregate, identifier-free result of one database health inspection."""

    database_path: Path
    report_schema_version: int = 1
    file_size_bytes: int | None = None
    build_id: str | None = None
    built_at_utc: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        """Return whether any blocking health check failed."""

        return any(check.status is CheckStatus.FAIL for check in self.checks)

    def status_counts(self) -> dict[str, int]:
        """Count health checks by status."""

        counts = {status.value: 0 for status in CheckStatus}
        for check in self.checks:
            counts[check.status.value] += 1
        return counts


def _check(
    code: str,
    status: CheckStatus,
    message: str,
    **metrics: Any,
) -> CheckResult:
    return CheckResult(code=code, status=status, message=message, metrics=metrics)


def _resolved_path(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _read_only_check(
    connection: duckdb.DuckDBPyConnection,
) -> CheckResult:
    try:
        connection.execute(
            "CREATE TABLE meta.__database_health_write_probe (value INTEGER)"
        )
    except duckdb.Error:
        return _check(
            "health.read_only_access",
            CheckStatus.PASS,
            "The health connection rejects persistent writes.",
        )

    connection.execute("DROP TABLE meta.__database_health_write_probe")
    return _check(
        "health.read_only_access",
        CheckStatus.FAIL,
        "The health connection unexpectedly permitted a persistent write.",
    )


def _build_metadata_checks(
    connection: duckdb.DuckDBPyConnection,
    report: DatabaseHealthReport,
) -> tuple[list[CheckResult], dict[str, int]]:
    rows = connection.execute(
        """
        SELECT
            build_id::VARCHAR,
            built_at_utc,
            source_key,
            source_row_count,
            loaded_schema,
            loaded_table,
            loaded_row_count,
            source_sha256,
            source_byte_size,
            git_commit,
            git_worktree_dirty
        FROM meta.build_info
        ORDER BY source_key
        """
    ).fetchall()
    checks: list[CheckResult] = []
    build_ids = {str(row[0]) for row in rows}
    built_at_values = {row[1] for row in rows}
    source_keys = {str(row[2]) for row in rows}
    expected_sources = {"customers", "transactions"}
    expected_tables = {
        "customers": ("source", "customers"),
        "transactions": ("source", "transactions"),
    }

    metadata_valid = (
        len(rows) == 2
        and len(build_ids) == 1
        and len(built_at_values) == 1
        and source_keys == expected_sources
        and all(int(row[3]) == int(row[6]) for row in rows)
        and all(
            (str(row[4]), str(row[5])) == expected_tables[str(row[2])] for row in rows
        )
        and all(bool(row[7]) and int(row[8]) > 0 for row in rows)
    )
    checks.append(
        _check(
            "health.build_metadata",
            CheckStatus.PASS if metadata_valid else CheckStatus.FAIL,
            (
                "Build metadata contains one complete, internally consistent build."
                if metadata_valid
                else "Build metadata is missing or internally inconsistent."
            ),
            metadata_rows=len(rows),
            distinct_build_ids=len(build_ids),
            source_keys=sorted(source_keys),
        )
    )

    expected_row_counts: dict[str, int] = {}
    if rows and len(build_ids) == 1:
        report.build_id = next(iter(build_ids))
    if rows and len(built_at_values) == 1:
        report.built_at_utc = next(iter(built_at_values))
    for row in rows:
        expected_row_counts[str(row[2])] = int(row[6])

    dirty_values = {bool(row[10]) for row in rows}
    if dirty_values == {True}:
        checks.append(
            _check(
                "health.build_provenance",
                CheckStatus.WARNING,
                "The database was built from a Git worktree with uncommitted changes.",
                git_commit=str(rows[0][9]) if rows[0][9] else None,
                git_worktree_dirty=True,
            )
        )
    elif dirty_values == {False}:
        checks.append(
            _check(
                "health.build_provenance",
                CheckStatus.PASS,
                "The database was built from a clean Git worktree.",
                git_commit=str(rows[0][9]) if rows[0][9] else None,
                git_worktree_dirty=False,
            )
        )
    else:
        checks.append(
            _check(
                "health.build_provenance",
                CheckStatus.FAIL,
                "Build provenance rows disagree about worktree state.",
            )
        )

    return checks, expected_row_counts


def _persisted_validation_checks(
    connection: duckdb.DuckDBPyConnection,
    report: DatabaseHealthReport,
) -> list[CheckResult]:
    rows = connection.execute(
        """
        SELECT
            build_id::VARCHAR,
            check_code,
            status,
            message,
            metrics::VARCHAR
        FROM meta.validation_results
        ORDER BY check_ordinal
        """
    ).fetchall()
    checks: list[CheckResult] = []
    build_ids = {str(row[0]) for row in rows}
    failed_rows = [row for row in rows if str(row[2]) == CheckStatus.FAIL.value]
    metadata_valid = (
        bool(rows)
        and len(build_ids) == 1
        and report.build_id in build_ids
        and not failed_rows
    )
    checks.append(
        _check(
            "health.persisted_validations",
            CheckStatus.PASS if metadata_valid else CheckStatus.FAIL,
            (
                "Persisted build validations contain no blocking failures."
                if metadata_valid
                else "Persisted build validations are missing, mismatched, or failed."
            ),
            validation_rows=len(rows),
            distinct_build_ids=len(build_ids),
            failed_rows=len(failed_rows),
        )
    )

    for row in rows:
        status = str(row[2])
        if status not in {CheckStatus.WARNING.value, CheckStatus.FAIL.value}:
            continue
        try:
            metrics = json.loads(str(row[4]))
        except json.JSONDecodeError:
            metrics = {"stored_metrics_unreadable": True}
        checks.append(
            _check(
                f"persisted.{row[1]}",
                CheckStatus(status),
                str(row[3]),
                **metrics,
            )
        )
    return checks


def _row_count_check(
    connection: duckdb.DuckDBPyConnection,
    expected: dict[str, int],
) -> tuple[CheckResult, dict[str, int]]:
    observed = {
        "customers": int(
            connection.execute("SELECT count(*) FROM source.customers").fetchone()[0]
        ),
        "transactions": int(
            connection.execute("SELECT count(*) FROM source.transactions").fetchone()[0]
        ),
    }
    valid = observed == expected
    return (
        _check(
            "health.row_counts",
            CheckStatus.PASS if valid else CheckStatus.FAIL,
            (
                "Current source-table row counts match build metadata."
                if valid
                else "Current source-table row counts differ from build metadata."
            ),
            expected=expected,
            observed=observed,
        ),
        observed,
    )


def _key_and_relationship_checks(
    connection: duckdb.DuckDBPyConnection,
    transaction_rows: int,
) -> list[CheckResult]:
    customer_metrics = connection.execute(
        """
        SELECT
            count_if(customer_id IS NULL),
            count(*) - count(DISTINCT customer_id)
        FROM source.customers
        """
    ).fetchone()
    customer_valid = customer_metrics == (0, 0)

    transaction_metrics = connection.execute(
        """
        SELECT
            count_if(transaction_row_id IS NULL),
            count(*) - count(DISTINCT transaction_row_id),
            min(transaction_row_id),
            max(transaction_row_id),
            count_if(
                customer_id IS NULL
                OR transaction_date IS NULL
                OR amount_cop IS NULL
                OR transaction_type IS NULL
                OR is_duplicate_looking IS NULL
            )
        FROM source.transactions
        """
    ).fetchone()
    transaction_valid = (
        transaction_metrics[0] == 0
        and transaction_metrics[1] == 0
        and transaction_metrics[2] == 1
        and transaction_metrics[3] == transaction_rows
        and transaction_metrics[4] == 0
    )

    orphan_rows = int(
        connection.execute(
            """
            SELECT count(*)
            FROM source.transactions AS transactions
            LEFT JOIN source.customers AS customers USING (customer_id)
            WHERE customers.customer_id IS NULL
            """
        ).fetchone()[0]
    )
    return [
        _check(
            "health.customer_keys",
            CheckStatus.PASS if customer_valid else CheckStatus.FAIL,
            (
                "Customer identifiers remain non-null and unique."
                if customer_valid
                else "Customer identifiers violate the key contract."
            ),
            null_ids=int(customer_metrics[0]),
            duplicate_id_rows=int(customer_metrics[1]),
        ),
        _check(
            "health.transaction_contract",
            CheckStatus.PASS if transaction_valid else CheckStatus.FAIL,
            (
                "Transaction keys and required values satisfy the table contract."
                if transaction_valid
                else "Transaction keys or required values violate the table contract."
            ),
            null_ids=int(transaction_metrics[0]),
            duplicate_id_rows=int(transaction_metrics[1]),
            minimum_id=(
                int(transaction_metrics[2])
                if transaction_metrics[2] is not None
                else None
            ),
            maximum_id=(
                int(transaction_metrics[3])
                if transaction_metrics[3] is not None
                else None
            ),
            required_null_rows=int(transaction_metrics[4]),
        ),
        _check(
            "health.transaction_customers",
            CheckStatus.PASS if orphan_rows == 0 else CheckStatus.FAIL,
            (
                "Every transaction still resolves to a customer."
                if orphan_rows == 0
                else "Some transactions no longer resolve to a customer."
            ),
            orphan_transaction_rows=orphan_rows,
        ),
    ]


def _ledger_check(
    connection: duckdb.DuckDBPyConnection,
) -> CheckResult:
    stored_row = connection.execute(
        """
        SELECT metrics::VARCHAR
        FROM meta.validation_results
        WHERE check_code = 'database.transaction_ledger'
        ORDER BY check_ordinal DESC
        LIMIT 1
        """
    ).fetchone()
    if stored_row is None:
        return _check(
            "health.transaction_ledger",
            CheckStatus.FAIL,
            "The persisted transaction-ledger baseline is missing.",
        )

    try:
        stored = json.loads(str(stored_row[0]))
        expected_total = Decimal(str(stored["expected_total_amount_cop"]))
        expected_minimum = date.fromisoformat(str(stored["expected_minimum_date"]))
        expected_maximum = date.fromisoformat(str(stored["expected_maximum_date"]))
    except (KeyError, ValueError, json.JSONDecodeError) as error:
        return _check(
            "health.transaction_ledger",
            CheckStatus.FAIL,
            "The persisted transaction-ledger baseline is unreadable.",
            error_type=type(error).__name__,
        )

    observed = connection.execute(
        """
        SELECT
            sum(amount_cop),
            min(transaction_date),
            max(transaction_date)
        FROM source.transactions
        """
    ).fetchone()
    valid = (
        observed[0] == expected_total
        and observed[1] == expected_minimum
        and observed[2] == expected_maximum
    )
    return _check(
        "health.transaction_ledger",
        CheckStatus.PASS if valid else CheckStatus.FAIL,
        (
            "The current ledger total and date range match the build baseline."
            if valid
            else "The current ledger differs from the persisted build baseline."
        ),
        expected_total_amount_cop=expected_total,
        observed_total_amount_cop=observed[0],
        expected_minimum_date=expected_minimum,
        observed_minimum_date=observed[1],
        expected_maximum_date=expected_maximum,
        observed_maximum_date=observed[2],
    )


def _duplicate_flag_check(
    connection: duckdb.DuckDBPyConnection,
) -> CheckResult:
    metrics = connection.execute(
        """
        WITH grouped AS (
            SELECT
                customer_id,
                transaction_date,
                amount_cop,
                transaction_type,
                count(*) AS rows_in_group
            FROM source.transactions
            GROUP BY
                customer_id,
                transaction_date,
                amount_cop,
                transaction_type
        )
        SELECT
            count(*) FILTER (WHERE rows_in_group > 1),
            coalesce(sum(rows_in_group - 1) FILTER (
                WHERE rows_in_group > 1
            ), 0),
            coalesce(sum(rows_in_group) FILTER (
                WHERE rows_in_group > 1
            ), 0),
            (SELECT count_if(is_duplicate_looking) FROM source.transactions)
        FROM grouped
        """
    ).fetchone()
    valid = int(metrics[2]) == int(metrics[3])
    return _check(
        "health.duplicate_looking_flags",
        CheckStatus.PASS if valid else CheckStatus.FAIL,
        (
            "Duplicate-looking flags match the current exact row groups."
            if valid
            else "Duplicate-looking flags differ from the current exact row groups."
        ),
        duplicate_groups=int(metrics[0]),
        excess_duplicate_rows=int(metrics[1]),
        rows_in_duplicate_groups=int(metrics[2]),
        flagged_rows=int(metrics[3]),
    )


def _fingerprint_check(
    connection: duckdb.DuckDBPyConnection,
    report: DatabaseHealthReport,
) -> CheckResult:
    customers = connection.execute(
        """
        SELECT count(*), bit_xor(hash(customers)),
            sum(hash(customers)::HUGEINT)
        FROM source.customers AS customers
        """
    ).fetchone()
    transactions = connection.execute(
        """
        SELECT count(*), bit_xor(hash(transactions)),
            sum(hash(transactions)::HUGEINT)
        FROM source.transactions AS transactions
        """
    ).fetchone()
    report.details["logical_fingerprints"] = {
        "customers": {
            "rows": int(customers[0]),
            "xor": int(customers[1]),
            "sum": int(customers[2]),
        },
        "transactions": {
            "rows": int(transactions[0]),
            "xor": int(transactions[1]),
            "sum": int(transactions[2]),
        },
    }
    return _check(
        "health.full_table_scan",
        CheckStatus.PASS,
        "Complete aggregate scans succeeded for both source tables.",
        customer_rows=int(customers[0]),
        transaction_rows=int(transactions[0]),
    )


def inspect_database_health(
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> DatabaseHealthReport:
    """Run a complete read-only logical health inspection."""

    resolved_path = _resolved_path(database_path)
    report = DatabaseHealthReport(database_path=resolved_path)
    if not resolved_path.is_file():
        report.checks.append(
            _check(
                "health.database_file",
                CheckStatus.FAIL,
                "The analytical database file is missing.",
            )
        )
        return report

    report.file_size_bytes = resolved_path.stat().st_size
    report.checks.append(
        _check(
            "health.database_file",
            (CheckStatus.PASS if report.file_size_bytes > 0 else CheckStatus.FAIL),
            (
                "The analytical database file exists and is nonempty."
                if report.file_size_bytes > 0
                else "The analytical database file is empty."
            ),
            byte_size=report.file_size_bytes,
        )
    )
    if report.has_failures:
        return report

    try:
        connection = open_database(resolved_path)
    except (OSError, duckdb.Error) as error:
        report.checks.append(
            _check(
                "health.database_open",
                CheckStatus.FAIL,
                "DuckDB could not open the database read-only.",
                error_type=type(error).__name__,
            )
        )
        return report

    try:
        report.checks.append(
            _check(
                "health.database_open",
                CheckStatus.PASS,
                "DuckDB opened the database read-only.",
            )
        )
        structure_checks = database_structure_checks(connection)
        report.checks.extend(structure_checks)
        if any(check.status is CheckStatus.FAIL for check in structure_checks):
            return report

        report.checks.append(_read_only_check(connection))
        metadata_checks, expected_rows = _build_metadata_checks(
            connection,
            report,
        )
        report.checks.extend(metadata_checks)
        report.checks.extend(_persisted_validation_checks(connection, report))

        row_count_check, observed_rows = _row_count_check(
            connection,
            expected_rows,
        )
        report.checks.append(row_count_check)
        report.details["row_counts"] = observed_rows
        report.checks.extend(
            _key_and_relationship_checks(
                connection,
                observed_rows["transactions"],
            )
        )
        report.checks.append(_ledger_check(connection))
        report.checks.append(_duplicate_flag_check(connection))
        report.checks.append(_fingerprint_check(connection, report))
        return report
    except (KeyError, TypeError, ValueError, duckdb.Error) as error:
        report.checks.append(
            _check(
                "health.complete_scan",
                CheckStatus.FAIL,
                "The database health scan could not complete.",
                error_type=type(error).__name__,
                error_message=str(error),
            )
        )
        return report
    finally:
        connection.close()
