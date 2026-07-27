"""Tests for the centralized command-line interface."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

import member_engagement_analytics.cli as cli_module
from member_engagement_analytics.cli import create_parser, main
from member_engagement_analytics.database import DatabaseBuildResult


@pytest.mark.parametrize(
    "command",
    (
        "preflight",
        "build-database",
        "database-health",
        "recency-analysis",
    ),
)
def test_parser_exposes_each_workflow_as_a_subcommand(command: str) -> None:
    args = create_parser().parse_args([command])

    assert args.command == command
    assert callable(args.command_handler)


def test_database_health_defaults_to_repository_report_path() -> None:
    args = create_parser().parse_args(["database-health"])

    assert args.output == Path("reports/database-health.json")


def test_build_database_subcommand_dispatches_to_build_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_directory = tmp_path / "raw"
    target_path = tmp_path / "database.duckdb"
    received: dict[str, object] = {}

    def fake_build_database(
        *,
        raw_directory: Path,
        target_path: Path,
        replace: bool,
    ) -> DatabaseBuildResult:
        received.update(
            raw_directory=raw_directory,
            target_path=target_path,
            replace=replace,
        )
        return DatabaseBuildResult(
            target_path=target_path,
            build_id=uuid4(),
            customer_rows=2,
            transaction_rows=3,
            warning_count=0,
            validation_count=4,
        )

    monkeypatch.setattr(cli_module, "build_database", fake_build_database)

    exit_code = main(
        [
            "build-database",
            "--raw-dir",
            str(raw_directory),
            "--target",
            str(target_path),
            "--replace",
        ]
    )

    assert exit_code == 0
    assert received == {
        "raw_directory": raw_directory,
        "target_path": target_path,
        "replace": True,
    }
    assert "Database build: PASS" in capsys.readouterr().out
