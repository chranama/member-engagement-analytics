"""Command-line entry point for the atomic COFINFAD database build."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from member_engagement_analytics.database import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_RAW_DIRECTORY,
    REPOSITORY_ROOT,
    DatabaseBuildError,
    DatabaseValidationError,
    build_database,
)


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse database-build command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Validate the raw COFINFAD files and atomically build the typed "
            "DuckDB analytical database."
        )
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIRECTORY,
        help="Raw CSV directory (default: data/raw).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=(
            "Generated DuckDB path (default: data/processed/member_engagement.duckdb)."
        ),
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Allow an existing generated database to be atomically replaced.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the build and map its outcome to the documented exit code."""

    args = parse_args(arguments)
    try:
        result = build_database(
            raw_directory=args.raw_dir,
            target_path=args.target,
            replace=args.replace,
        )
    except DatabaseValidationError as error:
        print(f"Database build: FAIL\n{error}", file=sys.stderr)
        for check in error.checks:
            print(f"- [FAIL] {check.code}: {check.message}", file=sys.stderr)
        return 1
    except (DatabaseBuildError, OSError) as error:
        print(
            f"Database build runtime failure ({type(error).__name__}): {error}",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # noqa: BLE001 - CLI boundary maps failures to code 2
        print(
            f"Database build runtime failure ({type(error).__name__}): {error}",
            file=sys.stderr,
        )
        return 2

    result_label = "PASS WITH WARNINGS" if result.warning_count else "PASS"
    print(
        "\n".join(
            [
                f"Database build: {result_label}",
                f"Target: {_display_path(result.target_path)}",
                f"Customers loaded: {result.customer_rows:,}",
                f"Transactions loaded: {result.transaction_rows:,}",
                "Blocking failures: 0",
                f"Warnings: {result.warning_count}",
                "Read-only verification: passed",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
