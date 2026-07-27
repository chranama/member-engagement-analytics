"""Tests for atomic database construction and read-only access."""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

import member_engagement_analytics.database as database_module
from member_engagement_analytics.database import (
    DatabaseBuildError,
    DatabaseValidationError,
    build_database,
    open_database,
)
from member_engagement_analytics.raw_data_validation import (
    CUSTOMER_HEADERS,
    CheckResult,
    CheckStatus,
    PreflightReport,
)


def _write_csv(
    path: Path,
    headers: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _write_fixture_sources(
    raw_directory: Path,
    *,
    orphan_transaction: bool = False,
) -> PreflightReport:
    raw_directory.mkdir()
    customer_path = raw_directory / "customer_data.csv"
    transaction_path = raw_directory / "transactions_data.csv"

    customer_rows = [
        {"customer_id": "101", "credit_card": "True"},
        {"customer_id": "202", "credit_card": "False"},
    ]
    transaction_rows = [
        {
            "customer_id": "999" if orphan_transaction else "101",
            "date": "2023-01-04",
            "amount": "10.00",
            "type": "Payment",
        },
        {
            "customer_id": "202",
            "date": "2023-01-05",
            "amount": "12.50",
            "type": "Transfer",
        },
        {
            "customer_id": "202",
            "date": "2023-01-05",
            "amount": "12.50",
            "type": "Transfer",
        },
    ]
    _write_csv(customer_path, CUSTOMER_HEADERS, customer_rows)
    _write_csv(
        transaction_path,
        ("customer_id", "date", "amount", "type"),
        transaction_rows,
    )

    report = PreflightReport(raw_directory=raw_directory.as_posix())
    report.files = {
        "customers": {
            "filename": customer_path.name,
            "data_rows": len(customer_rows),
            "byte_size": customer_path.stat().st_size,
            "sha256": hashlib.sha256(customer_path.read_bytes()).hexdigest(),
        },
        "transactions": {
            "filename": transaction_path.name,
            "data_rows": len(transaction_rows),
            "byte_size": transaction_path.stat().st_size,
            "sha256": hashlib.sha256(transaction_path.read_bytes()).hexdigest(),
        },
    }
    report.profiles = {
        "transactions": {
            "amount_cop": {"total": Decimal("35.00")},
            "date_range": {
                "minimum": date(2023, 1, 4),
                "maximum": date(2023, 1, 5),
            },
            "duplicate_looking_rows": {
                "duplicate_groups": 1,
                "excess_duplicate_rows": 1,
            },
        }
    }
    report.checks.append(
        CheckResult(
            code="transactions.duplicate_looking_rows",
            status=CheckStatus.WARNING,
            message="Fixture includes one duplicate-looking transaction group.",
            metrics={"duplicate_groups": 1, "excess_duplicate_rows": 1},
        )
    )
    return report


def test_builds_typed_database_and_opens_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_directory = tmp_path / "raw"
    report = _write_fixture_sources(raw_directory)
    target_path = tmp_path / "member_engagement.duckdb"
    monkeypatch.setattr(
        database_module,
        "inspect_raw_sources",
        lambda *args, **kwargs: report,
    )

    result = build_database(
        raw_directory=raw_directory,
        target_path=target_path,
    )

    assert result.customer_rows == 2
    assert result.transaction_rows == 3
    assert result.warning_count == 1
    assert target_path.is_file()
    assert not list(tmp_path.glob(".*.building-*.duckdb"))

    connection = open_database(target_path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM source.customers"
        ).fetchone() == (2,)
        assert connection.execute(
            """
            SELECT count_if(is_duplicate_looking)
            FROM source.transactions
            """
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM meta.build_info"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM meta.validation_results"
        ).fetchone() == (result.validation_count,)
        with pytest.raises(duckdb.Error):
            connection.execute("CREATE TABLE forbidden_write (value INTEGER)")
    finally:
        connection.close()


def test_existing_target_requires_explicit_replace(tmp_path: Path) -> None:
    target_path = tmp_path / "member_engagement.duckdb"
    target_path.write_bytes(b"existing")

    with pytest.raises(DatabaseBuildError, match="use --replace"):
        build_database(
            raw_directory=tmp_path / "raw",
            target_path=target_path,
        )

    assert target_path.read_bytes() == b"existing"


def test_failed_replacement_preserves_existing_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_directory = tmp_path / "raw"
    valid_report = _write_fixture_sources(raw_directory)
    target_path = tmp_path / "member_engagement.duckdb"
    monkeypatch.setattr(
        database_module,
        "inspect_raw_sources",
        lambda *args, **kwargs: valid_report,
    )
    build_database(raw_directory=raw_directory, target_path=target_path)
    original_digest = hashlib.sha256(target_path.read_bytes()).hexdigest()

    for path in raw_directory.iterdir():
        path.unlink()
    raw_directory.rmdir()
    orphan_report = _write_fixture_sources(
        raw_directory,
        orphan_transaction=True,
    )
    monkeypatch.setattr(
        database_module,
        "inspect_raw_sources",
        lambda *args, **kwargs: orphan_report,
    )

    with pytest.raises(DatabaseValidationError):
        build_database(
            raw_directory=raw_directory,
            target_path=target_path,
            replace=True,
        )

    assert hashlib.sha256(target_path.read_bytes()).hexdigest() == original_digest
    connection = open_database(target_path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM source.transactions"
        ).fetchone() == (3,)
    finally:
        connection.close()
