"""Produce the cardholder transaction-recency baseline artifacts."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from member_engagement_analytics.database import (
    DEFAULT_DATABASE_PATH,
    REPOSITORY_ROOT,
    open_database,
)
from member_engagement_analytics.render import write_recency_analysis_outputs

ANALYSIS_DATE = pd.Timestamp("2023-12-29")
EXPECTED_POPULATION = 30_460
EXPECTED_SCENARIO_COUNTS = {30: 5_835, 60: 1_686, 90: 457}
SCENARIOS = (
    (30, "More than 30 days"),
    (60, "More than 60 days"),
    (90, "More than 90 days"),
)
BAND_LABELS = ("0–30 days", "31–60 days", "61–90 days", "More than 90 days")

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

    write_recency_analysis_outputs(
        canonical=canonical,
        scenario_summary=scenarios,
        band_summary=bands,
        recency=recency,
        trajectory=trajectory,
        sensitivity=sensitivity,
        scenario_table_path=scenario_table_path,
        recency_figure_path=recency_figure_path,
        trajectory_figure_path=trajectory_figure_path,
        memo_path=memo_path,
    )

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
