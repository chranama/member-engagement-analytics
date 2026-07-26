# Python Analysis Environment Specification

## Decision

Use `uv` for Python installation, virtual-environment creation, dependency resolution, locking, synchronization, and command execution.

The environment will be intentionally small. Dependencies should be added only when they support the defined analytical workflow, not because they might be useful later.

## Python version

Pin the project to Python 3.12:

```text
.python-version: 3.12
requires-python: >=3.12,<3.13
```

Python 3.12 provides a mature package ecosystem and avoids coupling the project to the machine's current system Python. The local system currently defaults to Python 3.14, but project commands must run through `uv`, not through an unqualified system interpreter.

## Environment files

The environment contract consists of:

| File | Purpose | Commit to Git |
| --- | --- | --- |
| `pyproject.toml` | Project metadata, Python constraint, direct dependencies, and tool configuration | Yes |
| `.python-version` | Interpreter pin used by `uv` | Yes |
| `uv.lock` | Exact resolved direct and transitive dependency versions | Yes |
| `.venv/` | Local synchronized environment | No |

The project uses the `src/member_engagement_analytics/` package layout. The `uv_build` backend installs it into the project environment during `uv sync`, allowing project modules to be executed with `python -m` and imported consistently by tests and notebooks. This local installation does not imply that the package will be published.

## Direct dependencies

### Core

These packages support code executed outside notebooks:

| Dependency constraint | Purpose |
| --- | --- |
| `duckdb>=1.4,<1.5` | DuckDB 1.4 LTS database access and SQL execution |
| `numpy>=2.2,<3` | Explicit numerical operations used by analysis code |
| `pandas>=2.3,<3` | Tabular manipulation of bounded query results |

### Analysis group

These packages support interactive exploration and visualization:

| Dependency constraint | Purpose |
| --- | --- |
| `jupyterlab>=4.4,<5` | Local notebook interface |
| `ipykernel>=6.29,<8` | Project-specific Jupyter kernel |
| `matplotlib>=3.10,<4` | Base plotting and export |
| `seaborn>=0.13,<0.14` | Statistical visualization over pandas results |

### Development group

| Dependency constraint | Purpose |
| --- | --- |
| `pytest>=8.4,<10` | Automated validation of database, transformations, and analytical logic |
| `ruff>=0.12,<1` | Python linting and formatting |

## Proposed `pyproject.toml`

```toml
[project]
name = "member-engagement-analytics"
version = "0.1.0"
description = "Transaction engagement analysis using the COFINFAD dataset"
readme = "README.md"
requires-python = ">=3.12,<3.13"
dependencies = [
    "duckdb>=1.4,<1.5",
    "numpy>=2.2,<3",
    "pandas>=2.3,<3",
]

[build-system]
requires = ["uv_build>=0.11.32,<0.12"]
build-backend = "uv_build"

[dependency-groups]
analysis = [
    "ipykernel>=6.29,<8",
    "jupyterlab>=4.4,<5",
    "matplotlib>=3.10,<4",
    "seaborn>=0.13,<0.14",
]
dev = [
    "pytest>=8.4,<10",
    "ruff>=0.12,<1",
]

[tool.uv]
default-groups = ["analysis", "dev"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

The version ranges constrain intentional compatibility. `uv.lock` supplies exact reproducibility within those ranges.

## Explicit exclusions

Do not add these packages initially:

- `sqlalchemy`: DuckDB's Python client is sufficient for a single embedded database.
- `scikit-learn`: the current scope is descriptive and diagnostic analysis, not predictive modeling.
- `streamlit`, `dash`, or `plotly`: add a presentation framework only after choosing the stakeholder deliverable.
- `openpyxl`: no Excel deliverable has been defined.
- `pandera` or another validation framework: begin with SQL assertions and `pytest`; add one only if validation code becomes difficult to maintain.
- `python-dotenv`: the workflow has no secrets or external service credentials.

Transitive packages must not be declared as direct dependencies unless project code imports them or depends on their public API.

## Setup and execution contract

Initial creation:

```bash
uv python pin 3.12
uv lock
uv sync
```

Normal use:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked jupyter lab
```

`uv sync` performs an exact environment synchronization by default. Automated or reproducibility-sensitive commands must use `--locked` so a stale lockfile causes a failure instead of an implicit dependency change.

## Dependency-change policy

1. Add a dependency only for an identified requirement.
2. Declare it in the appropriate core, analysis, or development group.
3. Give direct dependencies a lower bound and a next-major or otherwise justified upper bound.
4. Regenerate `uv.lock`.
5. Run the full lint and test suite.
6. Review the dependency-tree change before committing.

Routine upgrades should be intentional rather than automatic:

```bash
uv lock --upgrade-package <package>
uv tree
uv run --locked pytest
```

## Notebook policy

- Use notebooks for exploration, explanation, and stakeholder-visible analysis.
- Put reusable loading, validation, metric, and plotting logic in `src/`.
- Keep SQL in `sql/` when it defines a reusable analytical transformation.
- Restart the kernel and run all cells before treating a notebook as complete.
- Do not load the entire transaction table into pandas unless a measured requirement justifies it.
- Use fixed random seeds for any sampling.
- Do not store local absolute paths, credentials, or raw-data copies in notebook output.

## Reproducibility and acceptance criteria

The Python environment is complete when:

- `uv sync --locked` creates the environment from a clean checkout.
- `uv run --locked python --version` reports Python 3.12.x.
- `uv lock --check` succeeds.
- `uv run --locked pytest` succeeds.
- `uv run --locked ruff check .` succeeds.
- Python can open the generated DuckDB database in read-only mode.
- The `.venv` directory and notebook checkpoints are excluded from Git.
- Another machine can reproduce the same locked environment without relying on the system Python.
