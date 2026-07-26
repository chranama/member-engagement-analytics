"""Opt-in integration test for the complete local COFINFAD dataset."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from member_engagement_analytics.database import (
    DEFAULT_RAW_DIRECTORY,
    build_database,
    open_database,
)

EXPECTED_CUSTOMER_FINGERPRINT = (
    48_723,
    17_588_890_083_138_199_500,
    449_304_469_143_078_242_044_318,
)
EXPECTED_TRANSACTION_FINGERPRINT = (
    3_159_157,
    15_768_486_669_893_320_456,
    29_151_025_005_070_057_740_262_790,
)


def _logical_fingerprints(database_path: Path) -> tuple[tuple, tuple]:
    connection = open_database(database_path)
    try:
        customers = connection.execute(
            """
            SELECT
                count(*),
                bit_xor(hash(c)),
                sum(hash(c)::HUGEINT)
            FROM source.customers AS c
            """
        ).fetchone()
        transactions = connection.execute(
            """
            SELECT
                count(*),
                bit_xor(hash(t)),
                sum(hash(t)::HUGEINT)
            FROM source.transactions AS t
            """
        ).fetchone()
        assert customers is not None
        assert transactions is not None
        return customers, transactions
    finally:
        connection.close()


@pytest.mark.full_data
@pytest.mark.skipif(
    os.environ.get("RUN_FULL_DATA_TESTS") != "1",
    reason="Set RUN_FULL_DATA_TESTS=1 to run the complete local-data build.",
)
def test_full_build_is_logically_reproducible(tmp_path: Path) -> None:
    target_path = tmp_path / "member_engagement.duckdb"

    first_result = build_database(
        raw_directory=DEFAULT_RAW_DIRECTORY,
        target_path=target_path,
    )
    first_fingerprints = _logical_fingerprints(target_path)

    second_result = build_database(
        raw_directory=DEFAULT_RAW_DIRECTORY,
        target_path=target_path,
        replace=True,
    )
    second_fingerprints = _logical_fingerprints(target_path)

    assert first_result.customer_rows == 48_723
    assert first_result.transaction_rows == 3_159_157
    assert first_result.warning_count == 3
    assert second_result.warning_count == 3
    assert first_fingerprints == second_fingerprints
    assert first_fingerprints == (
        EXPECTED_CUSTOMER_FINGERPRINT,
        EXPECTED_TRANSACTION_FINGERPRINT,
    )
