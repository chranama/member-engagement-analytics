"""Command-line orchestration for raw COFINFAD source inspection."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from member_engagement_analytics.reporting import (
    render_preflight_summary,
    write_preflight_json,
)
from member_engagement_analytics.validation import inspect_raw_sources

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RAW_DIRECTORY = Path("data/raw")
DEFAULT_OUTPUT_PATH = Path("reports/preflight-data.json")


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse preflight command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Inspect raw COFINFAD CSV files in memory without creating or "
            "modifying a DuckDB database."
        )
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=DEFAULT_RAW_DIRECTORY,
        help=(
            "Directory containing customer_data.csv and transactions_data.csv "
            "(default: data/raw relative to the repository root)."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=(
            "Path for the aggregate JSON report "
            "(default: reports/preflight-data.json relative to the repository root)."
        ),
    )
    return parser.parse_args(arguments)


def _repository_path(path: Path) -> Path:
    return path if path.is_absolute() else REPOSITORY_ROOT / path


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the preflight and return its documented process exit code."""

    args = parse_args(arguments)
    raw_directory = _repository_path(args.raw_dir)
    output_path = _repository_path(args.output)

    try:
        report = inspect_raw_sources(
            raw_directory,
            display_directory=args.raw_dir.as_posix(),
        )
        print(render_preflight_summary(report))
        write_preflight_json(report, output_path)
        print(f"\nJSON report: {args.output.as_posix()}")
    except Exception as error:  # noqa: BLE001 - CLI boundary maps failures to code 2
        print(
            f"Preflight runtime failure ({type(error).__name__}): {error}",
            file=sys.stderr,
        )
        return 2

    return 1 if report.has_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
