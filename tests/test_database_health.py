"""Tests for the read-only database artifact health inspection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from member_engagement_analytics.cli import main
from member_engagement_analytics.database import REPOSITORY_ROOT
from member_engagement_analytics.database_health import (
    DatabaseHealthReport,
    inspect_database_health,
)
from member_engagement_analytics.raw_data_validation import CheckStatus

BUILD_ID = "00000000-0000-0000-0000-000000000001"


def _create_healthy_database(database_path: Path) -> None:
    connection = duckdb.connect(database_path.as_posix())
    try:
        for filename in (
            "001_create_schemas.sql",
            "010_create_source_tables.sql",
            "020_create_meta_tables.sql",
        ):
            sql_path = REPOSITORY_ROOT / "sql/db_build" / filename
            connection.execute(sql_path.read_text(encoding="utf-8"))

        connection.execute(
            """
            INSERT INTO source.customers (customer_id, credit_card)
            VALUES (101, true)
            """
        )
        connection.execute(
            """
            INSERT INTO source.transactions (
                transaction_row_id,
                customer_id,
                transaction_date,
                amount_cop,
                transaction_type,
                is_duplicate_looking
            )
            VALUES (1, 101, DATE '2023-01-04', 10.00, 'Payment', false)
            """
        )

        built_at = datetime(2024, 1, 1, tzinfo=UTC)
        build_rows = [
            (
                BUILD_ID,
                built_at,
                "customers",
                "customer_data.csv",
                "source",
                "customers",
            ),
            (
                BUILD_ID,
                built_at,
                "transactions",
                "transactions_data.csv",
                "source",
                "transactions",
            ),
        ]
        for (
            build_id,
            timestamp,
            source_key,
            filename,
            loaded_schema,
            loaded_table,
        ) in build_rows:
            connection.execute(
                """
                INSERT INTO meta.build_info (
                    build_id,
                    built_at_utc,
                    source_key,
                    dataset_name,
                    dataset_version,
                    dataset_source_url,
                    source_filename,
                    source_sha256,
                    source_byte_size,
                    source_row_count,
                    loaded_schema,
                    loaded_table,
                    loaded_row_count,
                    git_commit,
                    git_worktree_dirty,
                    duckdb_version,
                    python_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    build_id,
                    timestamp,
                    source_key,
                    "COFINFAD",
                    "1",
                    "https://data.mendeley.com/datasets/mhb4zn3258/1",
                    filename,
                    "a" * 64,
                    100,
                    1,
                    loaded_schema,
                    loaded_table,
                    1,
                    "abc123",
                    False,
                    duckdb.__version__,
                    "3.12",
                ],
            )

        ledger_metrics = json.dumps(
            {
                "expected_total_amount_cop": "10.00",
                "expected_minimum_date": "2023-01-04",
                "expected_maximum_date": "2023-01-04",
            }
        )
        connection.execute(
            """
            INSERT INTO meta.validation_results (
                build_id,
                check_ordinal,
                check_code,
                status,
                message,
                metrics
            )
            VALUES (?, 1, ?, 'pass', ?, ?)
            """,
            [
                BUILD_ID,
                "database.transaction_ledger",
                "The transaction ledger matches its source baseline.",
                ledger_metrics,
            ],
        )
    finally:
        connection.close()


def _check_statuses(report: DatabaseHealthReport) -> dict[str, CheckStatus]:
    return {check.code: check.status for check in report.checks}


def test_healthy_database_passes_complete_read_only_inspection(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "healthy.duckdb"
    _create_healthy_database(database_path)

    report = inspect_database_health(database_path)
    statuses = _check_statuses(report)

    assert not report.has_failures
    assert report.build_id == BUILD_ID
    assert report.details["row_counts"] == {
        "customers": 1,
        "transactions": 1,
    }
    assert statuses["health.read_only_access"] is CheckStatus.PASS
    assert statuses["health.build_metadata"] is CheckStatus.PASS
    assert statuses["health.transaction_ledger"] is CheckStatus.PASS
    assert statuses["health.full_table_scan"] is CheckStatus.PASS


def test_health_inspection_detects_metadata_row_count_drift(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "drifted.duckdb"
    _create_healthy_database(database_path)
    connection = duckdb.connect(database_path.as_posix())
    try:
        connection.execute(
            """
            UPDATE meta.build_info
            SET loaded_row_count = 99
            WHERE source_key = 'transactions'
            """
        )
    finally:
        connection.close()

    report = inspect_database_health(database_path)
    statuses = _check_statuses(report)

    assert report.has_failures
    assert statuses["health.build_metadata"] is CheckStatus.FAIL
    assert statuses["health.row_counts"] is CheckStatus.FAIL


def test_missing_database_is_a_blocking_health_failure(tmp_path: Path) -> None:
    report = inspect_database_health(tmp_path / "missing.duckdb")

    assert report.has_failures
    assert report.checks[0].code == "health.database_file"
    assert report.checks[0].status is CheckStatus.FAIL


def test_health_cli_writes_aggregate_json_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "healthy.duckdb"
    output_path = tmp_path / "database-health.json"
    _create_healthy_database(database_path)

    exit_code = main(
        [
            "database-health",
            "--database",
            str(database_path),
            "--output",
            str(output_path),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert "Database health: PASS" in captured.out
    assert "JSON report:" in captured.out
    assert payload["build_id"] == BUILD_ID
    assert payload["summary"]["fail"] == 0
    assert "logical_fingerprints" in payload["details"]
