"""Central command-line interface for the analytics workflow."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from member_engagement_analytics.database import (
    REPOSITORY_ROOT,
    DatabaseBuildError,
    DatabaseValidationError,
    build_database,
)
from member_engagement_analytics.database_health import inspect_database_health
from member_engagement_analytics.raw_data_validation import inspect_raw_sources
from member_engagement_analytics.recency_analysis import run_analysis
from member_engagement_analytics.render import (
    render_artifact_location,
    render_database_build_failure,
    render_database_build_summary,
    render_database_health_summary,
    render_preflight_summary,
    render_recency_cli_summary,
    render_runtime_failure,
    write_database_health_json,
    write_preflight_json,
)

DEFAULT_CLI_RAW_DIRECTORY = Path("data/raw")
DEFAULT_CLI_DATABASE_PATH = Path("data/processed/member_engagement.duckdb")
DEFAULT_PREFLIGHT_OUTPUT_PATH = Path("reports/preflight-data.json")
DEFAULT_DATABASE_HEALTH_OUTPUT_PATH = Path("reports/database-health.json")
CommandHandler = Callable[[argparse.Namespace], int]


def _repository_path(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def _add_preflight_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "preflight",
        help="Inspect the raw CSV sources without persisting data.",
        description=(
            "Inspect raw COFINFAD CSV files in memory without creating or "
            "modifying a DuckDB database."
        ),
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_CLI_RAW_DIRECTORY,
        help="Directory containing the two raw COFINFAD CSV files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_PREFLIGHT_OUTPUT_PATH,
        help="Path for the aggregate JSON preflight report.",
    )
    parser.set_defaults(command_handler=_run_preflight)


def _add_build_database_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "build-database",
        help="Build the typed DuckDB analytical database.",
        description=(
            "Validate the raw COFINFAD files and atomically build the typed "
            "DuckDB analytical database."
        ),
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_CLI_RAW_DIRECTORY,
        help="Raw CSV directory.",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_CLI_DATABASE_PATH,
        help="Generated DuckDB path.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Allow an existing generated database to be atomically replaced.",
    )
    parser.set_defaults(command_handler=_run_build_database)


def _add_database_health_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "database-health",
        help="Inspect the current DuckDB artifact read-only.",
        description="Inspect the existing analytical DuckDB database read-only.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_CLI_DATABASE_PATH,
        help="Path to the generated DuckDB database.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DATABASE_HEALTH_OUTPUT_PATH,
        help="Path for the aggregate JSON health report.",
    )
    parser.set_defaults(command_handler=_run_database_health)


def _add_recency_analysis_command(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "recency-analysis",
        help="Produce the cardholder recency-baseline artifacts.",
        description="Produce the aggregate cardholder recency-baseline artifacts.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_CLI_DATABASE_PATH,
        help="Path to the generated DuckDB database.",
    )
    parser.set_defaults(command_handler=_run_recency_analysis)


def create_parser() -> argparse.ArgumentParser:
    """Create the complete project command-line parser."""

    parser = argparse.ArgumentParser(
        prog="member-engagement-analytics",
        description="Build, inspect, and analyze the local COFINFAD dataset.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="COMMAND",
        required=True,
    )
    _add_preflight_command(subparsers)
    _add_build_database_command(subparsers)
    _add_database_health_command(subparsers)
    _add_recency_analysis_command(subparsers)
    return parser


def _run_preflight(args: argparse.Namespace) -> int:
    raw_directory = _repository_path(args.raw_dir)
    output_path = _repository_path(args.output)
    try:
        report = inspect_raw_sources(
            raw_directory,
            display_directory=args.raw_dir.as_posix(),
        )
        print(render_preflight_summary(report))
        write_preflight_json(report, output_path)
        print()
        print(
            render_artifact_location(
                "JSON report",
                output_path,
                REPOSITORY_ROOT,
            )
        )
    except Exception as error:  # noqa: BLE001 - command boundary
        print(render_runtime_failure("Preflight", error), file=sys.stderr)
        return 2
    return 1 if report.has_failures else 0


def _run_build_database(args: argparse.Namespace) -> int:
    try:
        result = build_database(
            raw_directory=args.raw_dir,
            target_path=args.target,
            replace=args.replace,
        )
    except DatabaseValidationError as error:
        print(
            render_database_build_failure(str(error), error.checks),
            file=sys.stderr,
        )
        return 1
    except (DatabaseBuildError, OSError) as error:
        print(render_runtime_failure("Database build", error), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - command boundary
        print(render_runtime_failure("Database build", error), file=sys.stderr)
        return 2

    print(render_database_build_summary(result, REPOSITORY_ROOT))
    return 0


def _run_database_health(args: argparse.Namespace) -> int:
    try:
        report = inspect_database_health(args.database)
        print(render_database_health_summary(report))
        output_path = _repository_path(args.output)
        write_database_health_json(report, output_path)
        print()
        print(
            render_artifact_location(
                "JSON report",
                output_path,
                REPOSITORY_ROOT,
            )
        )
    except Exception as error:  # noqa: BLE001 - command boundary
        print(render_runtime_failure("Database health", error), file=sys.stderr)
        return 2
    return 1 if report.has_failures else 0


def _run_recency_analysis(args: argparse.Namespace) -> int:
    try:
        artifacts = run_analysis(database_path=args.database)
    except Exception as error:  # noqa: BLE001 - command boundary
        print(render_runtime_failure("Recency analysis", error), file=sys.stderr)
        return 1

    print(
        render_recency_cli_summary(
            len(artifacts.canonical),
            artifacts.output_paths,
            REPOSITORY_ROOT,
        )
    )
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Dispatch one project subcommand and return its process exit code."""

    args = create_parser().parse_args(arguments)
    handler: CommandHandler = args.command_handler
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
