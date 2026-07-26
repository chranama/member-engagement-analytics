"""Render deterministic aggregate reports from preflight inspection results."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from member_engagement_analytics.validation import (
    CheckResult,
    CheckStatus,
    PreflightReport,
)


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


def _check_as_dict(check: CheckResult) -> dict[str, Any]:
    return {
        "code": check.code,
        "status": check.status.value,
        "message": check.message,
        "metrics": _json_safe(check.metrics),
    }


def report_as_dict(report: PreflightReport) -> dict[str, Any]:
    """Convert a preflight report to a deterministic JSON-ready dictionary."""

    return {
        "report_schema_version": report.report_schema_version,
        "dataset": {
            "name": report.dataset_name,
            "version": report.dataset_version,
            "source_url": report.dataset_source_url,
        },
        "raw_directory": report.raw_directory,
        "summary": report.status_counts(),
        "files": _json_safe(report.files),
        "profiles": _json_safe(report.profiles),
        "checks": [_check_as_dict(check) for check in report.checks],
    }


def _result_label(report: PreflightReport) -> str:
    counts = report.status_counts()
    if counts[CheckStatus.FAIL.value]:
        return "FAIL"
    if counts[CheckStatus.WARNING.value]:
        return "PASS WITH WARNINGS"
    return "PASS"


def _format_number(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def render_preflight_summary(report: PreflightReport) -> str:
    """Produce the concise human-readable console report."""

    counts = report.status_counts()
    lines = [
        "COFINFAD raw-data preflight",
        f"Result: {_result_label(report)}",
        f"Raw directory: {report.raw_directory}",
        (
            "Checks: "
            f"{counts[CheckStatus.PASS.value]} passed, "
            f"{counts[CheckStatus.WARNING.value]} warnings, "
            f"{counts[CheckStatus.FAIL.value]} failed"
        ),
        "",
        "Files",
    ]

    for key in ("customers", "transactions"):
        file_profile = report.files.get(key, {})
        if not file_profile.get("present"):
            lines.append(f"- {key}: missing")
            continue
        rows = _format_number(file_profile.get("data_rows", "unknown"))
        byte_size = _format_number(file_profile.get("byte_size", "unknown"))
        checksum = str(file_profile.get("sha256", "unknown"))
        checksum_short = checksum[:12] if checksum != "unknown" else checksum
        lines.append(
            f"- {file_profile.get('filename', key)}: "
            f"{rows} data rows, {byte_size} bytes, SHA-256 {checksum_short}..."
        )

    if report.profiles:
        customer_profile = report.profiles.get("customers", {})
        transaction_profile = report.profiles.get("transactions", {})
        relationship_profile = report.profiles.get("relationships", {})
        transaction_dates = transaction_profile.get("date_range", {})
        transactions_per_customer = relationship_profile.get(
            "transactions_per_customer",
            {},
        )
        minimum_transactions = _format_number(
            transactions_per_customer.get("minimum", 0)
        )
        median_transactions = _format_number(transactions_per_customer.get("median", 0))
        mean_transactions = _format_number(transactions_per_customer.get("mean", 0))
        maximum_transactions = _format_number(
            transactions_per_customer.get("maximum", 0)
        )
        lines.extend(
            [
                "",
                "Profile",
                (
                    "- Credit-card holders: "
                    f"{_format_number(customer_profile.get('credit_card_holders', 0))}"
                ),
                (
                    "- Transaction date range: "
                    f"{transaction_dates.get('minimum', 'unknown')} to "
                    f"{transaction_dates.get('maximum', 'unknown')}"
                ),
                (
                    "- Transactions per represented customer: "
                    f"min {minimum_transactions}, median {median_transactions}, "
                    f"mean {mean_transactions}, max {maximum_transactions}"
                ),
            ]
        )

    lines.extend(["", "Checks"])
    status_symbols = {
        CheckStatus.PASS: "PASS",
        CheckStatus.WARNING: "WARN",
        CheckStatus.FAIL: "FAIL",
    }
    for check in report.checks:
        lines.append(
            f"- [{status_symbols[check.status]}] {check.code}: {check.message}"
        )

    return "\n".join(lines)


def write_preflight_json(
    report: PreflightReport,
    output_path: Path,
) -> None:
    """Atomically write the machine-readable preflight report."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report_as_dict(report),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            handle.write("\n")
            temporary_path = Path(handle.name)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
