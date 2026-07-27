"""Tests for cardholder recency aggregation and reporting."""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
import pytest

from member_engagement_analytics.recency_analysis import (
    CANONICAL_SQL_PATH,
    COLLAPSED_SQL_PATH,
    load_working_relation,
    sensitivity_statistics,
    summarize_bands,
    summarize_scenarios,
    trajectory_statistics,
    validate_working_relation,
)


def _working_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "customer_id": [101, 202, 303, 404],
            "first_transaction_date": pd.to_datetime(
                [
                    datetime(2023, 1, 4),
                    datetime(2023, 2, 1),
                    datetime(2023, 3, 1),
                    datetime(2023, 8, 1),
                ]
            ),
            "last_transaction_date": pd.to_datetime(
                [
                    datetime(2023, 12, 19),
                    datetime(2023, 11, 19),
                    datetime(2023, 10, 20),
                    datetime(2023, 9, 20),
                ]
            ),
            "observed_days": [360, 332, 304, 151],
            "recency_days": [10, 40, 70, 100],
            "transactions_full_period": [20, 15, 12, 10],
            "transactions_recent_90d": [5, 3, 1, 0],
            "transactions_prior_90d": [4, 4, 3, 0],
            "active_months": [10, 8, 7, 4],
            "sufficient_prior_history": [True, True, True, False],
        }
    )


def test_working_relation_and_summaries_follow_the_analysis_contract() -> None:
    canonical = _working_frame()
    collapsed = canonical.copy()
    collapsed.loc[0, "transactions_full_period"] = 19
    collapsed.loc[0, "transactions_recent_90d"] = 4

    validate_working_relation(
        canonical,
        expected_population=None,
        expected_scenario_counts=None,
    )
    scenarios = summarize_scenarios(canonical, collapsed)
    bands = summarize_bands(canonical)
    trajectory = trajectory_statistics(canonical)
    sensitivity = sensitivity_statistics(canonical, collapsed)

    canonical_scenarios = scenarios.loc[scenarios["data_variant"] == "canonical"]
    assert canonical_scenarios["customer_count"].tolist() == [3, 2, 1]
    assert "customer_id" not in scenarios.columns
    assert bands["customer_count"].tolist() == [1, 1, 1, 1]
    assert trajectory["customer_count"] == 3
    assert trajectory["lower_count"] == 2
    assert trajectory["equal_count"] == 0
    assert trajectory["higher_count"] == 1
    assert sensitivity["excess_rows_collapsed"] == 1
    assert sensitivity["affected_customers"] == 1
    assert sensitivity["recent_90d_rows_collapsed"] == 1
    assert sensitivity["maximum_absolute_scenario_count_change"] == 0


def test_working_relation_rejects_duplicate_customer_grain() -> None:
    frame = _working_frame()
    frame.loc[1, "customer_id"] = frame.loc[0, "customer_id"]

    with pytest.raises(ValueError, match="duplicate customer identifiers"):
        validate_working_relation(
            frame,
            expected_population=None,
            expected_scenario_counts=None,
        )


@pytest.mark.full_data
@pytest.mark.skipif(
    os.environ.get("RUN_FULL_DATA_TESTS") != "1",
    reason="Set RUN_FULL_DATA_TESTS=1 to run the complete local-data analysis.",
)
def test_full_recency_relations_satisfy_the_analysis_brief() -> None:
    canonical = load_working_relation(query_path=CANONICAL_SQL_PATH)
    collapsed = load_working_relation(query_path=COLLAPSED_SQL_PATH)

    validate_working_relation(canonical)
    validate_working_relation(collapsed)
    assert canonical["customer_id"].equals(collapsed["customer_id"])
    assert canonical["recency_days"].equals(collapsed["recency_days"])
    assert (
        sensitivity_statistics(
            canonical,
            collapsed,
        )["maximum_absolute_scenario_count_change"]
        == 0
    )
