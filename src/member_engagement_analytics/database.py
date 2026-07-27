"""Build and open the local COFINFAD analytical database."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import duckdb

from member_engagement_analytics.raw_data_validation import (
    CUSTOMER_TYPES,
    DATASET_NAME,
    DATASET_SOURCE_URL,
    DATASET_VERSION,
    SOURCE_SPECS,
    CheckResult,
    CheckStatus,
    PreflightReport,
    inspect_raw_sources,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATABASE_BUILD_SQL_DIRECTORY = REPOSITORY_ROOT / "sql/db_build"
DEFAULT_RAW_DIRECTORY = REPOSITORY_ROOT / "data/raw"
DEFAULT_DATABASE_PATH = REPOSITORY_ROOT / "data/processed/member_engagement.duckdb"

SCHEMA_SQL_FILES = (
    "001_create_schemas.sql",
    "010_create_source_tables.sql",
    "020_create_meta_tables.sql",
)
CUSTOMER_LOAD_SQL = "100_load_customers.sql"
TRANSACTION_LOAD_SQL = "110_load_transactions.sql"


class DatabaseBuildError(RuntimeError):
    """Base exception for a database build that did not complete."""


class DatabaseValidationError(DatabaseBuildError):
    """Raised when source or loaded data violates a blocking contract."""

    def __init__(self, message: str, checks: Sequence[CheckResult]) -> None:
        super().__init__(message)
        self.checks = tuple(checks)


@dataclass(frozen=True)
class DatabaseBuildResult:
    """Aggregate result returned after an atomic database build."""

    target_path: Path
    build_id: UUID
    customer_rows: int
    transaction_rows: int
    warning_count: int
    validation_count: int


def _resolved_repository_path(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _read_sql(filename: str) -> str:
    path = DATABASE_BUILD_SQL_DIRECTORY / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise DatabaseBuildError(f"Could not read SQL file {path}.") from error


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_safe(item) for item in value]
    return value


def _check(
    code: str,
    status: CheckStatus,
    message: str,
    **metrics: Any,
) -> CheckResult:
    return CheckResult(code=code, status=status, message=message, metrics=metrics)


def _fetch_scalar(
    connection: duckdb.DuckDBPyConnection,
    query: str,
) -> Any:
    row = connection.execute(query).fetchone()
    if row is None:
        raise DatabaseBuildError("Expected a scalar query result but received no rows.")
    return row[0]


def _table_definition(
    connection: duckdb.DuckDBPyConnection,
    qualified_table: str,
) -> list[tuple[str, str]]:
    rows = connection.execute(f"PRAGMA table_info('{qualified_table}')").fetchall()
    return [(str(row[1]), str(row[2]).upper()) for row in rows]


def _expected_customer_definition() -> list[tuple[str, str]]:
    return [(column, data_type.upper()) for column, data_type in CUSTOMER_TYPES.items()]


def database_structure_checks(
    connection: duckdb.DuckDBPyConnection,
) -> list[CheckResult]:
    """Validate the required database schemas, tables, columns, and types."""

    expected_schemas = {"source", "analytics", "meta"}
    observed_schemas = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name IN ('source', 'analytics', 'meta')
            """
        ).fetchall()
    }
    schema_match = observed_schemas == expected_schemas

    expected_customers = _expected_customer_definition()
    expected_transactions = [
        ("transaction_row_id", "BIGINT"),
        ("customer_id", "BIGINT"),
        ("transaction_date", "DATE"),
        ("amount_cop", "DECIMAL(20,2)"),
        ("transaction_type", "VARCHAR"),
        ("is_duplicate_looking", "BOOLEAN"),
    ]
    expected_build_info = [
        "build_id",
        "built_at_utc",
        "source_key",
        "dataset_name",
        "dataset_version",
        "dataset_source_url",
        "source_filename",
        "source_sha256",
        "source_byte_size",
        "source_row_count",
        "loaded_schema",
        "loaded_table",
        "loaded_row_count",
        "minimum_date",
        "maximum_date",
        "git_commit",
        "git_worktree_dirty",
        "duckdb_version",
        "python_version",
    ]
    expected_validation_results = [
        "build_id",
        "check_ordinal",
        "check_code",
        "status",
        "message",
        "metrics",
    ]

    observed_customers = _table_definition(connection, "source.customers")
    observed_transactions = _table_definition(connection, "source.transactions")
    observed_build_info = [
        column for column, _ in _table_definition(connection, "meta.build_info")
    ]
    observed_validation_results = [
        column for column, _ in _table_definition(connection, "meta.validation_results")
    ]
    tables_match = (
        observed_customers == expected_customers
        and observed_transactions == expected_transactions
        and observed_build_info == expected_build_info
        and observed_validation_results == expected_validation_results
    )

    return [
        _check(
            "database.schemas",
            CheckStatus.PASS if schema_match else CheckStatus.FAIL,
            (
                "The source, analytics, and meta schemas exist."
                if schema_match
                else "The database schema set is incomplete."
            ),
            expected=sorted(expected_schemas),
            observed=sorted(observed_schemas),
        ),
        _check(
            "database.table_definitions",
            CheckStatus.PASS if tables_match else CheckStatus.FAIL,
            (
                "Source and metadata tables have the expected columns and types."
                if tables_match
                else "One or more table definitions differ from the build contract."
            ),
            customer_columns=len(observed_customers),
            transaction_columns=len(observed_transactions),
            build_info_columns=len(observed_build_info),
            validation_result_columns=len(observed_validation_results),
        ),
    ]


def _post_load_checks(
    connection: duckdb.DuckDBPyConnection,
    report: PreflightReport,
) -> list[CheckResult]:
    checks = database_structure_checks(connection)

    customer_rows = int(
        _fetch_scalar(connection, "SELECT count(*) FROM source.customers")
    )
    transaction_rows = int(
        _fetch_scalar(connection, "SELECT count(*) FROM source.transactions")
    )
    expected_customer_rows = int(report.files["customers"]["data_rows"])
    expected_transaction_rows = int(report.files["transactions"]["data_rows"])
    counts_match = (
        customer_rows == expected_customer_rows
        and transaction_rows == expected_transaction_rows
    )
    checks.append(
        _check(
            "database.row_counts",
            CheckStatus.PASS if counts_match else CheckStatus.FAIL,
            (
                "Loaded row counts match the validated raw sources."
                if counts_match
                else "Loaded row counts differ from the validated raw sources."
            ),
            expected_customers=expected_customer_rows,
            loaded_customers=customer_rows,
            expected_transactions=expected_transaction_rows,
            loaded_transactions=transaction_rows,
        )
    )

    customer_key = connection.execute(
        """
        SELECT
            count_if(customer_id IS NULL),
            count(*) - count(DISTINCT customer_id)
        FROM source.customers
        """
    ).fetchone()
    customer_key_valid = customer_key == (0, 0)
    checks.append(
        _check(
            "database.customer_primary_key",
            CheckStatus.PASS if customer_key_valid else CheckStatus.FAIL,
            (
                "Loaded customer identifiers are non-null and unique."
                if customer_key_valid
                else "Loaded customer identifiers violate the primary-key contract."
            ),
            null_ids=int(customer_key[0]),
            duplicate_id_rows=int(customer_key[1]),
        )
    )

    transaction_key = connection.execute(
        """
        SELECT
            count_if(transaction_row_id IS NULL),
            count(*) - count(DISTINCT transaction_row_id),
            min(transaction_row_id),
            max(transaction_row_id)
        FROM source.transactions
        """
    ).fetchone()
    transaction_key_valid = (
        transaction_key[0] == 0
        and transaction_key[1] == 0
        and transaction_key[2] == 1
        and transaction_key[3] == transaction_rows
    )
    checks.append(
        _check(
            "database.transaction_surrogate_key",
            CheckStatus.PASS if transaction_key_valid else CheckStatus.FAIL,
            (
                "Transaction row identifiers are unique and sequential."
                if transaction_key_valid
                else "Transaction row identifiers violate the surrogate-key contract."
            ),
            null_ids=int(transaction_key[0]),
            duplicate_id_rows=int(transaction_key[1]),
            minimum_id=int(transaction_key[2])
            if transaction_key[2] is not None
            else None,
            maximum_id=int(transaction_key[3])
            if transaction_key[3] is not None
            else None,
        )
    )

    required_nulls = connection.execute(
        """
        SELECT
            count_if(customer_id IS NULL),
            count_if(transaction_date IS NULL),
            count_if(amount_cop IS NULL),
            count_if(transaction_type IS NULL),
            count_if(is_duplicate_looking IS NULL)
        FROM source.transactions
        """
    ).fetchone()
    required_valid = sum(int(value) for value in required_nulls) == 0
    checks.append(
        _check(
            "database.transaction_required_values",
            CheckStatus.PASS if required_valid else CheckStatus.FAIL,
            (
                "Required transaction fields contain no nulls."
                if required_valid
                else "Required transaction fields contain nulls."
            ),
            customer_id_nulls=int(required_nulls[0]),
            transaction_date_nulls=int(required_nulls[1]),
            amount_cop_nulls=int(required_nulls[2]),
            transaction_type_nulls=int(required_nulls[3]),
            duplicate_flag_nulls=int(required_nulls[4]),
        )
    )

    orphan_rows = int(
        _fetch_scalar(
            connection,
            """
            SELECT count(*)
            FROM source.transactions AS t
            LEFT JOIN source.customers AS c USING (customer_id)
            WHERE c.customer_id IS NULL
            """,
        )
    )
    checks.append(
        _check(
            "database.transaction_customers",
            CheckStatus.PASS if orphan_rows == 0 else CheckStatus.FAIL,
            (
                "Every loaded transaction resolves to a loaded customer."
                if orphan_rows == 0
                else "Some loaded transactions do not resolve to a customer."
            ),
            orphan_transaction_rows=orphan_rows,
        )
    )

    loaded_metrics = connection.execute(
        """
        SELECT
            sum(amount_cop),
            min(transaction_date),
            max(transaction_date)
        FROM source.transactions
        """
    ).fetchone()
    source_amount = report.profiles["transactions"]["amount_cop"]["total"]
    source_dates = report.profiles["transactions"]["date_range"]
    ledger_matches = (
        loaded_metrics[0] == source_amount
        and loaded_metrics[1] == source_dates["minimum"]
        and loaded_metrics[2] == source_dates["maximum"]
    )
    checks.append(
        _check(
            "database.transaction_ledger",
            CheckStatus.PASS if ledger_matches else CheckStatus.FAIL,
            (
                "Loaded amount total and date range match the validated raw ledger."
                if ledger_matches
                else "Loaded transaction metrics differ from the validated raw ledger."
            ),
            expected_total_amount_cop=source_amount,
            loaded_total_amount_cop=loaded_metrics[0],
            expected_minimum_date=source_dates["minimum"],
            loaded_minimum_date=loaded_metrics[1],
            expected_maximum_date=source_dates["maximum"],
            loaded_maximum_date=loaded_metrics[2],
        )
    )

    flagged_rows = int(
        _fetch_scalar(
            connection,
            """
            SELECT count_if(is_duplicate_looking)
            FROM source.transactions
            """,
        )
    )
    group_metrics = connection.execute(
        """
        SELECT
            count(*) AS duplicate_groups,
            coalesce(sum(rows_in_group - 1), 0) AS excess_duplicate_rows,
            coalesce(sum(rows_in_group), 0) AS rows_in_duplicate_groups
        FROM (
            SELECT count(*) AS rows_in_group
            FROM source.transactions
            GROUP BY
                customer_id,
                transaction_date,
                amount_cop,
                transaction_type
            HAVING count(*) > 1
        )
        """
    ).fetchone()
    expected_duplicates = report.profiles["transactions"]["duplicate_looking_rows"]
    duplicate_flag_valid = (
        flagged_rows == int(group_metrics[2])
        and int(group_metrics[0]) == int(expected_duplicates["duplicate_groups"])
        and int(group_metrics[1]) == int(expected_duplicates["excess_duplicate_rows"])
    )
    checks.append(
        _check(
            "database.duplicate_looking_flags",
            CheckStatus.PASS if duplicate_flag_valid else CheckStatus.FAIL,
            (
                "All duplicate-looking rows are retained and flagged."
                if duplicate_flag_valid
                else "Duplicate-looking flags do not match the loaded row groups."
            ),
            duplicate_groups=int(group_metrics[0]),
            excess_duplicate_rows=int(group_metrics[1]),
            rows_in_duplicate_groups=int(group_metrics[2]),
            flagged_rows=flagged_rows,
            expected_duplicate_groups=int(expected_duplicates["duplicate_groups"]),
            expected_excess_duplicate_rows=int(
                expected_duplicates["excess_duplicate_rows"]
            ),
        )
    )

    return checks


def _git_metadata() -> tuple[str | None, bool]:
    try:
        commit_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None, True
    return commit_result.stdout.strip() or None, bool(status_result.stdout.strip())


def _write_metadata(
    connection: duckdb.DuckDBPyConnection,
    *,
    build_id: UUID,
    built_at: datetime,
    report: PreflightReport,
    checks: Sequence[CheckResult],
) -> None:
    git_commit, git_worktree_dirty = _git_metadata()
    duckdb_version = duckdb.__version__
    python_version = platform.python_version()

    table_details = {
        "customers": ("source", "customers"),
        "transactions": ("source", "transactions"),
    }
    for spec in SOURCE_SPECS:
        source_profile = report.files[spec.key]
        schema_name, table_name = table_details[spec.key]
        loaded_rows = int(
            _fetch_scalar(
                connection,
                f'SELECT count(*) FROM "{schema_name}"."{table_name}"',
            )
        )
        date_range = (
            report.profiles["transactions"]["date_range"]
            if spec.key == "transactions"
            else {"minimum": None, "maximum": None}
        )
        connection.execute(
            """
            INSERT INTO meta.build_info VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                build_id,
                built_at,
                spec.key,
                DATASET_NAME,
                DATASET_VERSION,
                DATASET_SOURCE_URL,
                source_profile["filename"],
                source_profile["sha256"],
                source_profile["byte_size"],
                source_profile["data_rows"],
                schema_name,
                table_name,
                loaded_rows,
                date_range["minimum"],
                date_range["maximum"],
                git_commit,
                git_worktree_dirty,
                duckdb_version,
                python_version,
            ],
        )

    for ordinal, check in enumerate(checks, start=1):
        metrics_json = json.dumps(
            _json_safe(check.metrics),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        connection.execute(
            """
            INSERT INTO meta.validation_results VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                build_id,
                ordinal,
                check.code,
                check.status.value,
                check.message,
                metrics_json,
            ],
        )


def _verify_read_only(database_path: Path) -> None:
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        connection.execute("SELECT count(*) FROM source.customers").fetchone()
        connection.execute("SELECT count(*) FROM source.transactions").fetchone()
        try:
            connection.execute("CREATE TABLE read_only_probe (value INTEGER)")
        except duckdb.Error:
            return
        raise DatabaseBuildError(
            "Read-only verification unexpectedly permitted a write."
        )
    finally:
        connection.close()


def _temporary_database_path(target_path: Path) -> Path:
    return target_path.parent / (f".{target_path.name}.building-{uuid4().hex}.duckdb")


def _remove_temporary_database(path: Path) -> None:
    for candidate in (path, Path(f"{path}.wal")):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def build_database(
    *,
    raw_directory: Path = DEFAULT_RAW_DIRECTORY,
    target_path: Path = DEFAULT_DATABASE_PATH,
    replace: bool = False,
) -> DatabaseBuildResult:
    """Build, validate, and atomically publish the analytical database."""

    resolved_raw_directory = _resolved_repository_path(raw_directory)
    resolved_target_path = _resolved_repository_path(target_path)

    if resolved_target_path.exists() and not replace:
        raise DatabaseBuildError(
            f"Database already exists at {resolved_target_path}; "
            "use --replace to rebuild."
        )

    report = inspect_raw_sources(
        resolved_raw_directory,
        display_directory=raw_directory.as_posix(),
    )
    if report.has_failures:
        raise DatabaseValidationError(
            "Raw-source preflight contains blocking failures.",
            report.checks,
        )

    resolved_target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _temporary_database_path(resolved_target_path)
    build_id = uuid4()
    built_at = datetime.now(UTC)
    connection: duckdb.DuckDBPyConnection | None = None

    try:
        connection = duckdb.connect(str(temporary_path))
        for sql_filename in SCHEMA_SQL_FILES:
            connection.execute(_read_sql(sql_filename))

        customer_path = resolved_raw_directory / "customer_data.csv"
        transaction_path = resolved_raw_directory / "transactions_data.csv"
        connection.execute(_read_sql(CUSTOMER_LOAD_SQL), [str(customer_path)])
        connection.execute(_read_sql(TRANSACTION_LOAD_SQL), [str(transaction_path)])

        post_load_checks = _post_load_checks(connection, report)
        all_checks = [*report.checks, *post_load_checks]
        blocking_checks = [
            check for check in all_checks if check.status is CheckStatus.FAIL
        ]
        if blocking_checks:
            raise DatabaseValidationError(
                "Loaded database contains blocking validation failures.",
                blocking_checks,
            )

        _write_metadata(
            connection,
            build_id=build_id,
            built_at=built_at,
            report=report,
            checks=all_checks,
        )
        connection.execute("CHECKPOINT")
        connection.close()
        connection = None

        _verify_read_only(temporary_path)
        os.replace(temporary_path, resolved_target_path)

        customer_rows = int(report.files["customers"]["data_rows"])
        transaction_rows = int(report.files["transactions"]["data_rows"])
        warning_count = sum(check.status is CheckStatus.WARNING for check in all_checks)
        return DatabaseBuildResult(
            target_path=resolved_target_path,
            build_id=build_id,
            customer_rows=customer_rows,
            transaction_rows=transaction_rows,
            warning_count=warning_count,
            validation_count=len(all_checks),
        )
    except (duckdb.ConversionException, duckdb.ConstraintException) as error:
        check = _check(
            "database.typed_load",
            CheckStatus.FAIL,
            "A source row violated a declared type or table constraint.",
            error_type=type(error).__name__,
        )
        raise DatabaseValidationError(str(error), [check]) from error
    finally:
        if connection is not None:
            connection.close()
        if temporary_path.exists() or Path(f"{temporary_path}.wal").exists():
            _remove_temporary_database(temporary_path)


def open_database(
    database_path: Path | None = None,
    *,
    read_only: bool = True,
) -> duckdb.DuckDBPyConnection:
    """Open the analytical database read-only unless explicitly overridden."""

    path = _resolved_repository_path(database_path or DEFAULT_DATABASE_PATH)
    if read_only and not path.is_file():
        raise FileNotFoundError(f"Database does not exist at {path}.")
    return duckdb.connect(str(path), read_only=read_only)
