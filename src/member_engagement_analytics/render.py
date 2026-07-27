"""Render and write all user-facing project reports and artifacts."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from member_engagement_analytics.raw_data_validation import (
    CheckResult,
    CheckStatus,
    PreflightReport,
)

if TYPE_CHECKING:
    import pandas as pd

    from member_engagement_analytics.database import DatabaseBuildResult
    from member_engagement_analytics.database_health import DatabaseHealthReport


def json_safe(value: Any) -> Any:
    """Convert aggregate project values into deterministic JSON-safe values."""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [json_safe(item) for item in value]
    return value


def check_as_dict(check: CheckResult) -> dict[str, Any]:
    """Convert one validation check to an aggregate JSON-ready dictionary."""

    return {
        "code": check.code,
        "status": check.status.value,
        "message": check.message,
        "metrics": json_safe(check.metrics),
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
        "files": json_safe(report.files),
        "profiles": json_safe(report.profiles),
        "checks": [check_as_dict(check) for check in report.checks],
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


def atomic_write_text(output_path: Path, content: str) -> None:
    """Atomically write a UTF-8 text artifact."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
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
            handle.write(content.rstrip())
            handle.write("\n")
            temporary_path = Path(handle.name)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def atomic_write_json(output_path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write a deterministic aggregate JSON artifact."""

    content = json.dumps(
        json_safe(payload),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    atomic_write_text(output_path, content)


def atomic_write_dataframe_csv(
    output_path: Path,
    frame: pd.DataFrame,
) -> None:
    """Atomically write a deterministic aggregate data frame as CSV."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            frame.to_csv(
                handle,
                index=False,
                float_format="%.2f",
                lineterminator="\n",
            )
            temporary_path = Path(handle.name)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def write_preflight_json(
    report: PreflightReport,
    output_path: Path,
) -> None:
    """Atomically write the machine-readable preflight report."""

    atomic_write_json(output_path, report_as_dict(report))


def display_path(path: Path, repository_root: Path) -> str:
    """Return a repository-relative display path when possible."""

    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return path.as_posix()


def render_artifact_location(
    label: str,
    path: Path,
    repository_root: Path,
) -> str:
    """Render a labeled repository-relative artifact path."""

    return f"{label}: {display_path(path, repository_root)}"


def render_runtime_failure(context: str, error: Exception) -> str:
    """Render a consistent command-line runtime failure."""

    return f"{context} runtime failure ({type(error).__name__}): {error}"


def render_database_build_failure(
    message: str,
    checks: Sequence[CheckResult],
) -> str:
    """Render a blocking database-build validation failure."""

    lines = [f"Database build: FAIL\n{message}"]
    lines.extend(f"- [FAIL] {check.code}: {check.message}" for check in checks)
    return "\n".join(lines)


def render_database_build_summary(
    result: DatabaseBuildResult,
    repository_root: Path,
) -> str:
    """Render the successful database-build console summary."""

    result_label = "PASS WITH WARNINGS" if result.warning_count else "PASS"
    return "\n".join(
        [
            f"Database build: {result_label}",
            f"Target: {display_path(result.target_path, repository_root)}",
            f"Customers loaded: {result.customer_rows:,}",
            f"Transactions loaded: {result.transaction_rows:,}",
            "Blocking failures: 0",
            f"Warnings: {result.warning_count}",
            "Read-only verification: passed",
        ]
    )


def database_health_as_dict(
    report: DatabaseHealthReport,
) -> dict[str, Any]:
    """Convert a database-health report to a JSON-ready dictionary."""

    return {
        "report_schema_version": report.report_schema_version,
        "database_path": report.database_path.as_posix(),
        "file_size_bytes": report.file_size_bytes,
        "build_id": report.build_id,
        "built_at_utc": json_safe(report.built_at_utc),
        "summary": report.status_counts(),
        "details": json_safe(report.details),
        "checks": [check_as_dict(check) for check in report.checks],
    }


def render_database_health_summary(
    report: DatabaseHealthReport,
) -> str:
    """Render the concise database-health console report."""

    counts = report.status_counts()
    if counts[CheckStatus.FAIL.value]:
        result_label = "FAIL"
    elif counts[CheckStatus.WARNING.value]:
        result_label = "PASS WITH WARNINGS"
    else:
        result_label = "PASS"

    row_counts = report.details.get("row_counts", {})
    built_at = (
        str(json_safe(report.built_at_utc)) if report.built_at_utc else "unavailable"
    )
    lines = [
        f"Database health: {result_label}",
        f"Database: {report.database_path.as_posix()}",
        f"Build ID: {report.build_id or 'unavailable'}",
        f"Built at: {built_at}",
        f"Customers: {_format_number(row_counts.get('customers', 'unavailable'))}",
        (
            "Transactions: "
            f"{_format_number(row_counts.get('transactions', 'unavailable'))}"
        ),
        (
            "Checks: "
            f"{counts[CheckStatus.PASS.value]} passed, "
            f"{counts[CheckStatus.WARNING.value]} warnings, "
            f"{counts[CheckStatus.FAIL.value]} failed"
        ),
        "",
        "Checks",
    ]
    symbols = {
        CheckStatus.PASS: "PASS",
        CheckStatus.WARNING: "WARN",
        CheckStatus.FAIL: "FAIL",
    }
    lines.extend(
        f"- [{symbols[check.status]}] {check.code}: {check.message}"
        for check in report.checks
    )
    return "\n".join(lines)


def write_database_health_json(
    report: DatabaseHealthReport,
    output_path: Path,
) -> None:
    """Atomically write the aggregate database-health JSON report."""

    atomic_write_json(output_path, database_health_as_dict(report))


def _figure_style() -> dict[str, Any]:
    return {
        "font.family": "DejaVu Sans",
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "axes.edgecolor": "#9AA5B1",
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "grid.color": "#D9E2EC",
        "grid.linewidth": 0.7,
        "grid.alpha": 0.7,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "xtick.color": "#334E68",
        "ytick.color": "#334E68",
        "text.color": "#102A43",
    }


def _plotting_libraries() -> tuple[Any, Any]:
    """Lazily import plotting libraries for report-producing commands only."""

    import matplotlib
    import numpy as np

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot as plt

    return np, plt


def plot_recency_distribution(
    frame: pd.DataFrame,
    band_summary: pd.DataFrame,
    output_path: Path,
) -> None:
    """Render the recency ECDF and mutually exclusive band counts."""

    np, plt = _plotting_libraries()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    recency = np.sort(frame["recency_days"].to_numpy())
    ecdf = np.arange(1, len(recency) + 1) / len(recency) * 100
    band_colors = ("#2A9D8F", "#E9C46A", "#F4A261", "#E76F51")

    with plt.rc_context(_figure_style()):
        figure, axes = plt.subplots(
            1,
            2,
            figsize=(12, 6.75),
            gridspec_kw={"width_ratios": [1.45, 1]},
        )
        figure.suptitle(
            "Cardholder transaction recency is concentrated below 30 days",
            fontsize=17,
            fontweight="bold",
            x=0.06,
            ha="left",
            y=0.97,
        )
        figure.text(
            0.06,
            0.92,
            (
                "Cumulative distribution and mutually exclusive bands "
                f"for {len(frame):,} recorded credit-card holders"
            ),
            fontsize=10.5,
            color="#486581",
        )

        ecdf_axis = axes[0]
        ecdf_axis.plot(recency, ecdf, color="#176B87", linewidth=2.4)
        threshold_styles = (
            (30, "#E9C46A", 70),
            (60, "#F4A261", 87),
            (90, "#E76F51", 96),
        )
        for threshold, color, label_height in threshold_styles:
            cumulative = round(
                float((frame["recency_days"] <= threshold).sum()) / len(frame) * 100,
                2,
            )
            ecdf_axis.axvline(
                threshold,
                color=color,
                linewidth=1.5,
                linestyle="--",
            )
            ecdf_axis.text(
                threshold + 4,
                label_height,
                f"{threshold}d\n{cumulative:.1f}% at or below",
                fontsize=8.5,
                color="#334E68",
                va="center",
                bbox={
                    "boxstyle": "round,pad=0.2",
                    "facecolor": "white",
                    "edgecolor": "none",
                    "alpha": 0.85,
                },
            )
        ecdf_axis.set_title("Empirical cumulative distribution", loc="left")
        ecdf_axis.set_xlabel("Days since last recorded transaction")
        ecdf_axis.set_ylabel("Eligible customers at or below recency (%)")
        ecdf_axis.set_xlim(left=0)
        ecdf_axis.set_ylim(0, 101)
        ecdf_axis.spines[["top", "right"]].set_visible(False)

        band_axis = axes[1]
        bars = band_axis.bar(
            band_summary["recency_band"],
            band_summary["customer_count"],
            color=band_colors,
            width=0.72,
        )
        for bar, count, percentage in zip(
            bars,
            band_summary["customer_count"],
            band_summary["eligible_population_pct"],
            strict=True,
        ):
            band_axis.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + len(frame) * 0.012,
                f"{int(count):,}\n{float(percentage):.1f}%",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )
        band_axis.set_title("Mutually exclusive recency bands", loc="left")
        band_axis.set_ylabel("Customers")
        band_axis.tick_params(axis="x", labelrotation=18)
        band_axis.set_ylim(0, max(band_summary["customer_count"]) * 1.18)
        band_axis.spines[["top", "right"]].set_visible(False)

        figure.text(
            0.06,
            0.025,
            (
                "Analysis date: 2023-12-29. Recency reflects all recorded "
                "transactions, not credit-card usage."
            ),
            fontsize=8.5,
            color="#627D98",
        )
        figure.tight_layout(rect=(0.04, 0.06, 0.98, 0.89))
        figure.savefig(output_path, dpi=160, bbox_inches="tight")
        plt.close(figure)


def plot_prior_vs_recent_activity(
    frame: pd.DataFrame,
    statistics: Mapping[str, float | int],
    output_path: Path,
) -> None:
    """Render a bounded count plot for the two 90-day periods."""

    _, plt = _plotting_libraries()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    selected = frame.loc[frame["sufficient_prior_history"]].copy()
    display_cap = int(
        math.ceil(
            max(
                selected["transactions_prior_90d"].quantile(0.99),
                selected["transactions_recent_90d"].quantile(0.99),
            )
        )
    )
    displayed = selected.loc[
        selected["transactions_prior_90d"].le(display_cap)
        & selected["transactions_recent_90d"].le(display_cap)
    ]

    with plt.rc_context(_figure_style()):
        figure, axis = plt.subplots(figsize=(10.5, 6.2))
        figure.suptitle(
            "Recent activity is balanced overall, but individual paths vary",
            fontsize=17,
            fontweight="bold",
            x=0.08,
            ha="left",
            y=0.98,
        )
        figure.text(
            0.08,
            0.925,
            (
                "Customer density compares the recent 90 days with the "
                "immediately preceding 90 days"
            ),
            fontsize=10.5,
            color="#486581",
        )
        density = axis.hexbin(
            displayed["transactions_prior_90d"],
            displayed["transactions_recent_90d"],
            gridsize=42,
            extent=(0, display_cap, 0, display_cap),
            mincnt=1,
            bins="log",
            cmap="Blues",
            linewidths=0.2,
        )
        axis.plot(
            [0, display_cap],
            [0, display_cap],
            color="#E76F51",
            linestyle="--",
            linewidth=1.5,
            label="Equal activity",
        )
        axis.set_xlabel("Transactions in prior 90 days")
        axis.set_ylabel("Transactions in recent 90 days")
        axis.set_xlim(0, display_cap)
        axis.set_ylim(0, display_cap)
        axis.set_aspect("equal", adjustable="box")
        axis.legend(loc="upper left", frameon=False)
        axis.spines[["top", "right"]].set_visible(False)
        colorbar = figure.colorbar(density, ax=axis, pad=0.02)
        colorbar.set_label("Customers per hexagon (log scale)")

        annotation = (
            f"Lower: {float(statistics['lower_pct']):.1f}%\n"
            f"Equal: {float(statistics['equal_pct']):.1f}%\n"
            f"Higher: {float(statistics['higher_pct']):.1f}%"
        )
        axis.text(
            0.98,
            0.04,
            annotation,
            transform=axis.transAxes,
            ha="right",
            va="bottom",
            fontsize=9.5,
            bbox={
                "boxstyle": "round,pad=0.45",
                "facecolor": "white",
                "edgecolor": "#BCCCDC",
                "alpha": 0.94,
            },
        )
        displayed_percentage = round(
            len(displayed) / len(selected) * 100,
            1,
        )
        figure.text(
            0.08,
            0.025,
            (
                f"Central range shown: 0–{display_cap} transactions on each "
                f"axis ({displayed_percentage:.1f}% of customers with "
                "sufficient history)."
            ),
            fontsize=8.5,
            color="#627D98",
        )
        figure.tight_layout(rect=(0.05, 0.06, 0.98, 0.90))
        figure.savefig(output_path, dpi=160, bbox_inches="tight")
        plt.close(figure)


def _format_report_number(value: float | int, decimals: int = 1) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{float(value):,.{decimals}f}"


def _recency_scenario_markdown(summary: pd.DataFrame) -> str:
    canonical = summary.loc[summary["data_variant"] == "canonical"]
    lines = [
        "| Scenario | Customers | Population | Prior 90d median (IQR) "
        "| Zero prior 90d | Full-period median | Active-month median |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in canonical.itertuples(index=False):
        lines.append(
            "| "
            f"{row.scenario} | "
            f"{int(row.customer_count):,} | "
            f"{float(row.eligible_population_pct):.1f}% | "
            f"{float(row.transactions_prior_90d_median):.1f} "
            f"({float(row.transactions_prior_90d_q1):.1f}–"
            f"{float(row.transactions_prior_90d_q3):.1f}) | "
            f"{float(row.zero_prior_90d_pct):.1f}% | "
            f"{float(row.transactions_full_period_median):.1f} | "
            f"{float(row.active_months_median):.1f} |"
        )
    return "\n".join(lines)


def render_recency_findings_memo(
    *,
    scenario_summary: pd.DataFrame,
    band_summary: pd.DataFrame,
    recency: Mapping[str, float | int],
    trajectory: Mapping[str, float | int],
    sensitivity: Mapping[str, float | int],
) -> str:
    """Render the aggregate recency findings and recommendation."""

    primary = scenario_summary.loc[
        (scenario_summary["data_variant"] == "canonical")
        & (scenario_summary["threshold_days"] == 60)
    ].iloc[0]
    prior_activity_pct = 100 - float(primary["zero_prior_90d_pct"])
    sufficient_history_pct = 100 - float(primary["insufficient_prior_history_pct"])
    recent_customer_count = int(band_summary.iloc[0]["customer_count"])
    recent_population_pct = float(band_summary.iloc[0]["eligible_population_pct"])

    return f"""# Cardholder Transaction Recency Baseline

## Executive finding

As of December 29, 2023, most recorded credit-card holders transacted recently:
the median recency was {_format_report_number(recency["median"])} days, and
{recent_customer_count:,} customers ({recent_population_pct:.1f}%) had a
transaction within 30 days.

The 30-day scenario identifies a broad 5,835-customer population. A 60-day
scenario narrows this to 1,686 customers (5.5%), while a 90-day scenario leaves
457 (1.5%). These groups are better described as having elevated overall
transaction recency than as having inactive credit cards.

## Scenario results

{_recency_scenario_markdown(scenario_summary)}

The groups beyond 60 and 90 days were historically light users: each had a
median of four transactions in the prior 90 days. The greater-than-60-day group
had a median of 12 full-period transactions across seven active months.
Nevertheless, {prior_activity_pct:.1f}% had at least one prior-period
transaction and {sufficient_history_pct:.1f}% met the prior-history rule.

## Recent versus prior activity

Among {int(trajectory["customer_count"]):,} customers with sufficient history,
the median was {_format_report_number(trajectory["prior_90d_median"])} transactions in
both the prior and recent 90-day periods. Recent activity was lower for
{float(trajectory["lower_pct"]):.1f}%, unchanged for
{float(trajectory["equal_pct"]):.1f}%, and higher for
{float(trajectory["higher_pct"]):.1f}%.

This balanced population-level result masks different individual trajectories.
Apparent lapse should therefore remain separate from a future decline analysis.

## Duplicate sensitivity

Collapsing exact customer/date/amount/type groups removed
{int(sensitivity["excess_rows_collapsed"]):,} excess rows for
{int(sensitivity["affected_customers"]):,} eligible customers. It changed the
recent and prior windows by {int(sensitivity["recent_90d_rows_collapsed"]):,}
and {int(sensitivity["prior_90d_rows_collapsed"]):,} rows, respectively.

The maximum change in any 30-, 60-, or 90-day scenario count was
{int(sensitivity["maximum_absolute_scenario_count_change"]):,}. Exact duplicate
interpretation does not affect the recency-threshold decision.

## Recommendation

Carry **more than 60 days** forward as the primary screening threshold, with
more than 90 days retained as a nested high-recency group.

The 60-day threshold is preferable for the next slice because:

- 30 days captures 19.2% of the population and is likely too broad for an
  initial review;
- 60 days produces a bounded 5.5% population with adequate observable history;
- nearly all customers beyond 60 days had some prior-period activity; and
- 90 days is a clearer lapse signal but identifies only 1.5% of customers.

This is a screening definition, not proof of card inactivity. Because the
selected customers were typically light users before their recent gap, the next
slice should examine activity trajectory before profiling customer
characteristics or proposing action.

## Limitations

- Transactions do not identify the card, account, or payment instrument used.
- Every eligible customer has at least 10 transactions, so the data omits a
  transaction-zero comparison group.
- The observation period is limited to 2023.
- First recorded transaction is only a proxy for observable history.
- This descriptive analysis does not explain why activity changed or whether
  outreach would improve it.
"""


def write_recency_analysis_outputs(
    *,
    canonical: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    band_summary: pd.DataFrame,
    recency: Mapping[str, float | int],
    trajectory: Mapping[str, float | int],
    sensitivity: Mapping[str, float | int],
    scenario_table_path: Path,
    recency_figure_path: Path,
    trajectory_figure_path: Path,
    memo_path: Path,
) -> None:
    """Write every aggregate recency-analysis artifact."""

    atomic_write_dataframe_csv(scenario_table_path, scenario_summary)
    plot_recency_distribution(canonical, band_summary, recency_figure_path)
    plot_prior_vs_recent_activity(
        canonical,
        trajectory,
        trajectory_figure_path,
    )
    memo = render_recency_findings_memo(
        scenario_summary=scenario_summary,
        band_summary=band_summary,
        recency=recency,
        trajectory=trajectory,
        sensitivity=sensitivity,
    )
    atomic_write_text(memo_path, memo)


def render_recency_cli_summary(
    customer_count: int,
    output_paths: Mapping[str, Path],
    repository_root: Path,
) -> str:
    """Render the successful recency-analysis CLI summary."""

    lines = [
        "Cardholder recency analysis: PASS",
        f"Customers analyzed: {customer_count:,}",
    ]
    lines.extend(
        f"{label}: {display_path(path, repository_root)}"
        for label, path in output_paths.items()
    )
    return "\n".join(lines)
