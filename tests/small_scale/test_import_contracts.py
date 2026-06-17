"""Architectural fitness functions: the import contracts are enforced, not just
documented (H2, ADR-034).

These run import-linter's contracts (declared in [tool.importlinter] in
pyproject.toml) so a structural violation, observability reaching into a
context, one context importing another, or a bounded-context type leaking into
core, fails the build rather than waiting for a reviewer to catch it by eye.
This is the "enforce, don't document" move applied to the one structural
guarantee the architecture reviews asked to be made mechanical.

import-linter is run in a subprocess, not in-process. Its CLI configures the
stdlib logging and builds the whole-package import graph (importing every app
module); both would perturb the session-configured observability sink and the
structlog globals that other small_scale tests assert on. A subprocess isolates
all of it, so the gate cannot leak state into the rest of the suite, and it is
how import-linter actually runs in CI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Repo root and src, resolved from this file so the test holds wherever pytest
#: is invoked. pyproject carries the [tool.importlinter] contracts; src must be
#: importable in the subprocess so grimp can build the `app` graph.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _run_lint_imports(config_filename: str) -> subprocess.CompletedProcess[str]:
    """Run import-linter against a config in an isolated subprocess.

    Uses the same interpreter (the venv, where import-linter is installed) with
    src on PYTHONPATH so the `app` package is importable for the graph build.
    """
    pythonpath = os.pathsep.join([str(_SRC), os.environ.get("PYTHONPATH", "")])
    env = {**os.environ, "PYTHONPATH": pythonpath}
    code = (
        "import sys;"
        "from importlinter.cli import lint_imports, EXIT_STATUS_SUCCESS;"
        f"rc = lint_imports(config_filename={config_filename!r}, no_cache=True);"
        "sys.exit(0 if rc == EXIT_STATUS_SUCCESS else 1)"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_all_declared_import_contracts_hold() -> None:
    """Given the architecture's declared import contracts, when import-linter
    checks them against the real dependency graph, then all hold: observability
    imports no bounded context, the contexts are mutually independent, and core
    imports no bounded context (H2, ADR-034).
    """
    result = _run_lint_imports(str(_PYPROJECT))

    assert result.returncode == 0, (
        "import-linter reported a broken architectural contract:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_a_violating_contract_is_reported_broken(tmp_path: Path) -> None:
    """The gate has teeth, so it cannot pass vacuously.

    A contract that forbids a dependency the code really has (the Coordinator
    imports the Triage context, which is its job) is reported broken. Without
    this control, a contract whose module names were mistyped would match
    nothing and pass silently, and the guarantee would be hollow. This proves
    import-linter sees this codebase's real edges and would equally catch
    observability reaching into a context.
    """
    config = tmp_path / "neg_control.ini"
    config.write_text(
        "[importlinter]\n"
        "root_package = app\n"
        "\n"
        "[importlinter:contract:neg]\n"
        "name = negative control - pipeline must not import triage\n"
        "type = forbidden\n"
        "source_modules =\n"
        "    app.pipeline\n"
        "forbidden_modules =\n"
        "    app.triage\n",
        encoding="utf-8",
    )

    result = _run_lint_imports(str(config))

    assert result.returncode != 0, (
        "import-linter did not flag a dependency the code really has; the "
        f"gate may be vacuous:\n{result.stdout}\n{result.stderr}"
    )
