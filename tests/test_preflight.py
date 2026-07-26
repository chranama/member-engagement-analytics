"""Tests for preflight control flow and aggregate reporting."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from member_engagement_analytics.preflight import main
from member_engagement_analytics.reporting import (
    render_preflight_summary,
    write_preflight_json,
)
from member_engagement_analytics.validation import (
    CheckResult,
    CheckStatus,
    PreflightReport,
    inspect_raw_sources,
)


def test_missing_source_files_are_blocking(tmp_path: Path) -> None:
    report = inspect_raw_sources(tmp_path, display_directory="data/raw")

    assert report.has_failures
    assert report.status_counts() == {"pass": 0, "warning": 0, "fail": 2}
    assert {check.code for check in report.checks} == {
        "customers.file_exists",
        "transactions.file_exists",
    }


def test_report_writes_json_safe_aggregate_values(tmp_path: Path) -> None:
    report = PreflightReport(raw_directory="data/raw")
    report.profiles["example"] = {
        "amount": Decimal("12.30"),
        "date": date(2023, 1, 4),
    }
    report.checks.append(
        CheckResult(
            code="example.warning",
            status=CheckStatus.WARNING,
            message="Aggregate result requires review.",
            metrics={"affected_rows": 2},
        )
    )
    output_path = tmp_path / "preflight.json"

    write_preflight_json(report, output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["profiles"]["example"] == {
        "amount": "12.30",
        "date": "2023-01-04",
    }
    assert payload["summary"] == {"pass": 0, "warning": 1, "fail": 0}
    assert payload["checks"][0]["metrics"] == {"affected_rows": 2}


def test_console_summary_distinguishes_warnings_from_failures() -> None:
    report = PreflightReport(raw_directory="data/raw")
    report.checks.append(
        CheckResult(
            code="example.warning",
            status=CheckStatus.WARNING,
            message="Review this aggregate.",
        )
    )

    summary = render_preflight_summary(report)

    assert "Result: PASS WITH WARNINGS" in summary
    assert "0 failed" in summary
    assert "[WARN] example.warning" in summary


def test_cli_returns_one_for_blocking_data_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "preflight.json"

    exit_code = main(
        [
            "--raw-dir",
            str(tmp_path / "missing"),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Result: FAIL" in captured.out
    assert output_path.is_file()
