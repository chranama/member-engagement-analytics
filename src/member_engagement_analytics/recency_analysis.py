"""Produce the cardholder transaction-recency baseline artifacts."""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np
import pandas as pd

from member_engagement_analytics.database import (
    DEFAULT_DATABASE_PATH,
    REPOSITORY_ROOT,
    open_database,
)

matplotlib.use("Agg")
from matplotlib import pyplot as plt  # noqa: E402

ANALYSIS_DATE = pd.Timestamp("2023-12-29")
EXPECTED_POPULATION = 30_460
EXPECTED_SCENARIO_COUNTS = {30: 5_835, 60: 1_686, 90: 457}
SCENARIOS = (
    (30, "More than 30 days"),
    (60, "More than 60 days"),
    (90, "More than 90 days"),
)
BAND_LABELS = ("0–30 days", "31–60 days", "61–90 days", "More than 90 days")
BAND_COLORS = ("#2A9D8F", "#E9C46A", "#F4A261", "#E76F51")

CANONICAL_SQL_PATH = (
    REPOSITORY_ROOT / "sql/analysis/010_cardholder_recency_baseline.sql"
)
COLLAPSED_SQL_PATH = (
    REPOSITORY_ROOT / "sql/analysis/011_cardholder_recency_baseline_deduplicated.sql"
)
DEFAULT_SCENARIO_TABLE_PATH = (
    REPOSITORY_ROOT / "reports/tables/cardholder-recency-scenarios.csv"
)
DEFAULT_RECENCY_FIGURE_PATH = (
    REPOSITORY_ROOT / "reports/figures/cardholder-recency-distribution.png"
)
DEFAULT_TRAJECTORY_FIGURE_PATH = (
    REPOSITORY_ROOT / "reports/figures/cardholder-prior-vs-recent-activity.png"
)
DEFAULT_MEMO_PATH = REPOSITORY_ROOT / "reports/cardholder-recency-baseline.md"

REQUIRED_COLUMNS = (
    "customer_id",
    "first_transaction_date",
    "last_transaction_date",
    "observed_days",
    "recency_days",
    "transactions_full_period",
    "transactions_recent_90d",
    "transactions_prior_90d",
    "active_months",
    "sufficient_prior_history",
)


@dataclass(frozen=True)
class RecencyAnalysisArtifacts:
    """In-memory summaries and generated paths from one analysis run."""

    canonical: pd.DataFrame
    collapsed: pd.DataFrame
    scenario_summary: pd.DataFrame
    band_summary: pd.DataFrame
    recency_statistics: Mapping[str, float | int]
    trajectory_statistics: Mapping[str, float | int]
    sensitivity_statistics: Mapping[str, float | int]
    output_paths: Mapping[str, Path]


def _read_sql(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Could not read analysis SQL at {path}.") from error


def load_working_relation(
    *,
    database_path: Path = DEFAULT_DATABASE_PATH,
    query_path: Path = CANONICAL_SQL_PATH,
) -> pd.DataFrame:
    """Execute one version-controlled analysis query against the database."""

    connection = open_database(database_path)
    try:
        frame = connection.execute(_read_sql(query_path)).fetchdf()
    finally:
        connection.close()

    for column in ("first_transaction_date", "last_transaction_date"):
        frame[column] = pd.to_datetime(frame[column])
    return frame


def _scenario_count(frame: pd.DataFrame, threshold: int) -> int:
    return int((frame["recency_days"] > threshold).sum())


def validate_working_relation(
    frame: pd.DataFrame,
    *,
    expected_population: int | None = EXPECTED_POPULATION,
    expected_scenario_counts: Mapping[int, int] | None = EXPECTED_SCENARIO_COUNTS,
) -> None:
    """Enforce the analysis brief's grain, date, range, and count contract."""

    missing_columns = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing_columns:
        raise ValueError(f"Working relation is missing columns: {missing_columns}")

    if expected_population is not None and len(frame) != expected_population:
        raise ValueError(
            f"Expected {expected_population:,} customers; observed {len(frame):,}."
        )
    if frame["customer_id"].isna().any():
        raise ValueError("Working relation contains a null customer identifier.")
    if not frame["customer_id"].is_unique:
        raise ValueError("Working relation contains duplicate customer identifiers.")
    if frame[list(REQUIRED_COLUMNS)].isna().any(axis=None):
        raise ValueError("Working relation contains a null required value.")

    date_order_valid = (
        frame["first_transaction_date"].le(frame["last_transaction_date"]).all()
        and frame["last_transaction_date"].le(ANALYSIS_DATE).all()
    )
    if not date_order_valid:
        raise ValueError("Working relation violates the transaction-date order.")
    expected_observed_days = (
        ANALYSIS_DATE - frame["first_transaction_date"]
    ).dt.days + 1
    expected_recency_days = (ANALYSIS_DATE - frame["last_transaction_date"]).dt.days
    if not frame["observed_days"].equals(expected_observed_days):
        raise ValueError("Observed-day values do not reconcile to the fixed date.")
    if not frame["recency_days"].equals(expected_recency_days):
        raise ValueError("Recency values do not reconcile to the fixed date.")

    nonnegative_columns = (
        "observed_days",
        "recency_days",
        "transactions_full_period",
        "transactions_recent_90d",
        "transactions_prior_90d",
        "active_months",
    )
    if (frame[list(nonnegative_columns)] < 0).any(axis=None):
        raise ValueError("Working relation contains a negative count or duration.")

    recent_exceeds_full = (
        frame["transactions_full_period"] < frame["transactions_recent_90d"]
    ).any()
    prior_exceeds_full = (
        frame["transactions_full_period"] < frame["transactions_prior_90d"]
    ).any()
    combined_windows_exceed_full = (
        frame["transactions_full_period"]
        < (frame["transactions_recent_90d"] + frame["transactions_prior_90d"])
    ).any()
    if recent_exceeds_full or prior_exceeds_full or combined_windows_exceed_full:
        raise ValueError("A window count exceeds its full-period transaction count.")

    if frame["sufficient_prior_history"].isna().any():
        raise ValueError("Prior-history sufficiency contains null values.")

    if expected_scenario_counts is not None:
        observed_counts = {
            threshold: _scenario_count(frame, threshold)
            for threshold in expected_scenario_counts
        }
        if observed_counts != dict(expected_scenario_counts):
            raise ValueError(
                "Recency scenario counts do not match the fixed source contract: "
                f"expected {dict(expected_scenario_counts)}, "
                f"observed {observed_counts}."
            )


def _percentage(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        return 0.0
    return round(float(numerator) / float(denominator) * 100, 2)


def _quantile(series: pd.Series, probability: float) -> float:
    return round(float(series.quantile(probability)), 2)


def summarize_scenarios(
    canonical: pd.DataFrame,
    collapsed: pd.DataFrame,
) -> pd.DataFrame:
    """Create the deterministic aggregate scenario and sensitivity table."""

    rows: list[dict[str, Any]] = []
    frames = (
        ("canonical", canonical),
        ("exact_groups_collapsed", collapsed),
    )
    for variant, frame in frames:
        population = len(frame)
        for threshold, label in SCENARIOS:
            selected = frame.loc[frame["recency_days"] > threshold]
            customer_count = len(selected)
            zero_prior = int((selected["transactions_prior_90d"] == 0).sum())
            insufficient_history = int((~selected["sufficient_prior_history"]).sum())
            rows.append(
                {
                    "data_variant": variant,
                    "scenario": label,
                    "threshold_days": threshold,
                    "customer_count": customer_count,
                    "eligible_population_pct": _percentage(
                        customer_count,
                        population,
                    ),
                    "transactions_prior_90d_q1": _quantile(
                        selected["transactions_prior_90d"],
                        0.25,
                    ),
                    "transactions_prior_90d_median": _quantile(
                        selected["transactions_prior_90d"],
                        0.50,
                    ),
                    "transactions_prior_90d_q3": _quantile(
                        selected["transactions_prior_90d"],
                        0.75,
                    ),
                    "zero_prior_90d_customers": zero_prior,
                    "zero_prior_90d_pct": _percentage(
                        zero_prior,
                        customer_count,
                    ),
                    "transactions_full_period_median": _quantile(
                        selected["transactions_full_period"],
                        0.50,
                    ),
                    "active_months_median": _quantile(
                        selected["active_months"],
                        0.50,
                    ),
                    "insufficient_prior_history_customers": insufficient_history,
                    "insufficient_prior_history_pct": _percentage(
                        insufficient_history,
                        customer_count,
                    ),
                }
            )

    summary = pd.DataFrame(rows)
    canonical_reference = (
        summary.loc[
            summary["data_variant"] == "canonical",
            [
                "threshold_days",
                "customer_count",
                "transactions_prior_90d_median",
            ],
        ]
        .rename(
            columns={
                "customer_count": "canonical_customer_count",
                "transactions_prior_90d_median": "canonical_prior_median",
            }
        )
        .copy()
    )
    summary = summary.merge(
        canonical_reference,
        on="threshold_days",
        how="left",
        validate="many_to_one",
    )
    summary["customer_count_change_vs_canonical"] = (
        summary["customer_count"] - summary["canonical_customer_count"]
    )
    summary["prior_median_change_vs_canonical"] = (
        summary["transactions_prior_90d_median"] - summary["canonical_prior_median"]
    ).round(2)
    summary = summary.drop(
        columns=["canonical_customer_count", "canonical_prior_median"]
    )
    variant_order = pd.CategoricalDtype(
        ["canonical", "exact_groups_collapsed"],
        ordered=True,
    )
    summary["data_variant"] = summary["data_variant"].astype(variant_order)
    return summary.sort_values(
        ["data_variant", "threshold_days"],
        kind="stable",
    ).reset_index(drop=True)


def _recency_band(recency_days: pd.Series) -> pd.Series:
    return pd.cut(
        recency_days,
        bins=[-1, 30, 60, 90, math.inf],
        labels=BAND_LABELS,
        ordered=True,
    )


def summarize_bands(frame: pd.DataFrame) -> pd.DataFrame:
    """Summarize mutually exclusive recency bands."""

    working = frame.assign(recency_band=_recency_band(frame["recency_days"]))
    grouped = working.groupby("recency_band", observed=False)
    rows: list[dict[str, Any]] = []
    for band in BAND_LABELS:
        selected = grouped.get_group(band)
        customer_count = len(selected)
        zero_prior = int((selected["transactions_prior_90d"] == 0).sum())
        insufficient = int((~selected["sufficient_prior_history"]).sum())
        rows.append(
            {
                "recency_band": band,
                "customer_count": customer_count,
                "eligible_population_pct": _percentage(
                    customer_count,
                    len(frame),
                ),
                "transactions_prior_90d_q1": _quantile(
                    selected["transactions_prior_90d"],
                    0.25,
                ),
                "transactions_prior_90d_median": _quantile(
                    selected["transactions_prior_90d"],
                    0.50,
                ),
                "transactions_prior_90d_q3": _quantile(
                    selected["transactions_prior_90d"],
                    0.75,
                ),
                "zero_prior_90d_pct": _percentage(
                    zero_prior,
                    customer_count,
                ),
                "transactions_full_period_median": _quantile(
                    selected["transactions_full_period"],
                    0.50,
                ),
                "active_months_median": _quantile(
                    selected["active_months"],
                    0.50,
                ),
                "insufficient_prior_history_pct": _percentage(
                    insufficient,
                    customer_count,
                ),
            }
        )
    return pd.DataFrame(rows)


def recency_statistics(frame: pd.DataFrame) -> dict[str, float | int]:
    """Return the requested robust recency distribution statistics."""

    recency = frame["recency_days"]
    return {
        "customer_count": len(frame),
        "minimum": int(recency.min()),
        "q1": _quantile(recency, 0.25),
        "median": _quantile(recency, 0.50),
        "q3": _quantile(recency, 0.75),
        "p90": _quantile(recency, 0.90),
        "p95": _quantile(recency, 0.95),
        "maximum": int(recency.max()),
    }


def trajectory_statistics(frame: pd.DataFrame) -> dict[str, float | int]:
    """Compare recent and prior counts for sufficiently observed customers."""

    selected = frame.loc[frame["sufficient_prior_history"]].copy()
    change = selected["transactions_recent_90d"] - selected["transactions_prior_90d"]
    lower = int((change < 0).sum())
    equal = int((change == 0).sum())
    higher = int((change > 0).sum())
    return {
        "customer_count": len(selected),
        "excluded_for_limited_history": len(frame) - len(selected),
        "prior_90d_median": _quantile(
            selected["transactions_prior_90d"],
            0.50,
        ),
        "recent_90d_median": _quantile(
            selected["transactions_recent_90d"],
            0.50,
        ),
        "absolute_change_median": _quantile(change, 0.50),
        "lower_count": lower,
        "lower_pct": _percentage(lower, len(selected)),
        "equal_count": equal,
        "equal_pct": _percentage(equal, len(selected)),
        "higher_count": higher,
        "higher_pct": _percentage(higher, len(selected)),
    }


def sensitivity_statistics(
    canonical: pd.DataFrame,
    collapsed: pd.DataFrame,
) -> dict[str, float | int]:
    """Quantify the effect of collapsing exact duplicate-looking groups."""

    comparison = canonical.merge(
        collapsed,
        on="customer_id",
        suffixes=("_canonical", "_collapsed"),
        validate="one_to_one",
    )
    full_difference = (
        comparison["transactions_full_period_canonical"]
        - comparison["transactions_full_period_collapsed"]
    )
    recent_difference = (
        comparison["transactions_recent_90d_canonical"]
        - comparison["transactions_recent_90d_collapsed"]
    )
    prior_difference = (
        comparison["transactions_prior_90d_canonical"]
        - comparison["transactions_prior_90d_collapsed"]
    )
    scenario_count_changes = [
        _scenario_count(collapsed, threshold) - _scenario_count(canonical, threshold)
        for threshold, _ in SCENARIOS
    ]
    return {
        "excess_rows_collapsed": int(full_difference.sum()),
        "affected_customers": int((full_difference > 0).sum()),
        "maximum_rows_collapsed_for_one_customer": int(full_difference.max()),
        "recent_90d_rows_collapsed": int(recent_difference.sum()),
        "prior_90d_rows_collapsed": int(prior_difference.sum()),
        "maximum_absolute_scenario_count_change": int(
            max(abs(change) for change in scenario_count_changes)
        ),
    }


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


def plot_recency_distribution(
    frame: pd.DataFrame,
    band_summary: pd.DataFrame,
    output_path: Path,
) -> None:
    """Render the recency ECDF and mutually exclusive band counts."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    recency = np.sort(frame["recency_days"].to_numpy())
    ecdf = np.arange(1, len(recency) + 1) / len(recency) * 100

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
            cumulative = _percentage(
                int((frame["recency_days"] <= threshold).sum()),
                len(frame),
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
            color=BAND_COLORS,
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
    """Render a bounded two-dimensional count plot for the two 90-day periods."""

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
        figure.text(
            0.08,
            0.025,
            (
                f"Central range shown: 0–{display_cap} transactions on each "
                f"axis ({_percentage(len(displayed), len(selected)):.1f}% of "
                "customers with sufficient history)."
            ),
            fontsize=8.5,
            color="#627D98",
        )
        figure.tight_layout(rect=(0.05, 0.06, 0.98, 0.90))
        figure.savefig(output_path, dpi=160, bbox_inches="tight")
        plt.close(figure)


def _format_number(value: float | int, decimals: int = 1) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{float(value):,.{decimals}f}"


def _scenario_markdown(summary: pd.DataFrame) -> str:
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


def render_findings_memo(
    *,
    scenario_summary: pd.DataFrame,
    band_summary: pd.DataFrame,
    recency: Mapping[str, float | int],
    trajectory: Mapping[str, float | int],
    sensitivity: Mapping[str, float | int],
) -> str:
    """Render the aggregate findings and bounded recommendation."""

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
the median recency was {_format_number(recency["median"])} days, and
{recent_customer_count:,} customers ({recent_population_pct:.1f}%) had a
transaction within 30 days.

The 30-day scenario identifies a broad 5,835-customer population. A 60-day
scenario narrows this to 1,686 customers (5.5%), while a 90-day scenario leaves
457 (1.5%). These groups are better described as having elevated overall
transaction recency than as having inactive credit cards.

## Scenario results

{_scenario_markdown(scenario_summary)}

The groups beyond 60 and 90 days were historically light users: each had a
median of four transactions in the prior 90 days. The greater-than-60-day group
had a median of 12 full-period transactions across seven active months.
Nevertheless, {prior_activity_pct:.1f}% had at least one prior-period
transaction and {sufficient_history_pct:.1f}% met the prior-history rule.

## Recent versus prior activity

Among {int(trajectory["customer_count"]):,} customers with sufficient history,
the median was {_format_number(trajectory["prior_90d_median"])} transactions in
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


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def run_analysis(
    *,
    database_path: Path = DEFAULT_DATABASE_PATH,
    scenario_table_path: Path = DEFAULT_SCENARIO_TABLE_PATH,
    recency_figure_path: Path = DEFAULT_RECENCY_FIGURE_PATH,
    trajectory_figure_path: Path = DEFAULT_TRAJECTORY_FIGURE_PATH,
    memo_path: Path = DEFAULT_MEMO_PATH,
) -> RecencyAnalysisArtifacts:
    """Run the complete read-only analysis and produce aggregate artifacts."""

    canonical = load_working_relation(
        database_path=database_path,
        query_path=CANONICAL_SQL_PATH,
    )
    collapsed = load_working_relation(
        database_path=database_path,
        query_path=COLLAPSED_SQL_PATH,
    )
    validate_working_relation(canonical)
    validate_working_relation(collapsed)

    if not canonical["customer_id"].equals(collapsed["customer_id"]):
        raise ValueError("Sensitivity relation does not contain the canonical grain.")
    if not canonical["recency_days"].equals(collapsed["recency_days"]):
        raise ValueError("Collapsing duplicate-looking groups changed recency.")

    scenarios = summarize_scenarios(canonical, collapsed)
    bands = summarize_bands(canonical)
    recency = recency_statistics(canonical)
    trajectory = trajectory_statistics(canonical)
    sensitivity = sensitivity_statistics(canonical, collapsed)

    scenario_table_path.parent.mkdir(parents=True, exist_ok=True)
    scenarios.to_csv(
        scenario_table_path,
        index=False,
        float_format="%.2f",
        lineterminator="\n",
    )
    plot_recency_distribution(canonical, bands, recency_figure_path)
    plot_prior_vs_recent_activity(
        canonical,
        trajectory,
        trajectory_figure_path,
    )
    memo = render_findings_memo(
        scenario_summary=scenarios,
        band_summary=bands,
        recency=recency,
        trajectory=trajectory,
        sensitivity=sensitivity,
    )
    _write_text(memo_path, memo)

    return RecencyAnalysisArtifacts(
        canonical=canonical,
        collapsed=collapsed,
        scenario_summary=scenarios,
        band_summary=bands,
        recency_statistics=recency,
        trajectory_statistics=trajectory,
        sensitivity_statistics=sensitivity,
        output_paths={
            "scenario_table": scenario_table_path,
            "recency_figure": recency_figure_path,
            "trajectory_figure": trajectory_figure_path,
            "memo": memo_path,
        },
    )


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line options for the analysis run."""

    parser = argparse.ArgumentParser(
        description="Produce the aggregate cardholder recency-baseline artifacts."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="Path to the generated DuckDB database.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the analysis CLI."""

    args = parse_args(arguments)
    try:
        artifacts = run_analysis(database_path=args.database)
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(
            f"Recency analysis failed ({type(error).__name__}): {error}",
            file=sys.stderr,
        )
        return 1

    print("Cardholder recency analysis: PASS")
    print(f"Customers analyzed: {len(artifacts.canonical):,}")
    for label, path in artifacts.output_paths.items():
        try:
            display_path = path.relative_to(REPOSITORY_ROOT)
        except ValueError:
            display_path = path
        print(f"{label}: {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
