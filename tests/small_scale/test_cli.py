"""Behaviour tests for the CLI composition root (python -m app).

Bootstrap precedes everything and fails clean with an actionable message;
show-document round-trips the raw store through the context's own layout
helper and rejects malformed ids at the boundary; every CLI run records its
toolset as the registered startup_config event. The process command needs
the spaCy masker and the statute corpus and is covered by the medium-scale
CLI smoke instead.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import pytest

import app.__main__ as cli
from app.__main__ import main
from app.document_ingestion.service import DocumentIngestionService
from app.observability.events import CLI_UNHANDLED_ERROR, STARTUP_CONFIG
from app.observability.logging_config import LOG_FILENAME
from tests.conftest import FakePiiMasker

_ORIGINAL_TEXT = "Originaltext der Einwendung mit allen Details."


def _read_sink(log_dir: Path) -> list[dict]:
    """Flush the root handlers and parse the sink's JSON lines."""
    for handler in logging.getLogger().handlers:
        handler.flush()
    log_file = log_dir / LOG_FILENAME
    if not log_file.exists():
        return []
    return [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_bootstrap_failure_aborts_clean_with_exit_code_2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An impossible log directory is a clean startup abort, not a traceback.

    Given a log dir that cannot be created (its parent is an existing file),
    when the CLI starts, then it exits with code 2 and a stderr message that
    names the path, and no traceback is dumped.
    """
    occupied = tmp_path / "occupied"
    occupied.write_text("a file, not a directory", encoding="utf-8")
    impossible_dir = occupied / "logs"

    exit_code = main(
        ["--log-dir", str(impossible_dir), "show-document", str(uuid.uuid4())]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "startup aborted" in captured.err
    assert str(impossible_dir) in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_show_document_round_trips_a_stored_document(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """show-document prints exactly what ingestion stored for the id.

    Given a document stored by the ingestion service, when show-document is
    invoked with its id and store, then the original raw text is printed and
    the exit code is 0.
    """
    raw_store = tmp_path / "raw"
    ingestion = DocumentIngestionService(
        raw_store_path=raw_store,
        masker=FakePiiMasker(),
    )
    stored = ingestion.ingest(_ORIGINAL_TEXT)

    exit_code = main(
        [
            "--log-dir",
            str(tmp_path / "logs"),
            "show-document",
            stored.document_id,
            "--raw-store",
            str(raw_store),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert _ORIGINAL_TEXT in captured.out


def test_show_document_with_unknown_id_errors_clearly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unknown id is a clear error naming the id, and a nonzero exit."""
    unknown_id = str(uuid.uuid4())

    exit_code = main(
        [
            "--log-dir",
            str(tmp_path / "logs"),
            "show-document",
            unknown_id,
            "--raw-store",
            str(tmp_path / "raw"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert unknown_id in captured.err
    assert captured.out == ""


def test_show_document_rejects_a_path_shaped_id_at_the_boundary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-UUID id is rejected before any path is built (traversal guard)."""
    exit_code = main(
        [
            "--log-dir",
            str(tmp_path / "logs"),
            "show-document",
            "..\\..\\secrets",
            "--raw-store",
            str(tmp_path / "raw"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not a valid document id" in captured.err


def test_unexpected_exception_is_one_clean_line_and_a_governed_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected exception exits 1 with one clean line, no traceback.

    Given a command path that raises an exception outside the routed failure
    classes, when the CLI runs, then stderr carries exactly one line naming
    the exception type but not its message, no traceback is dumped, the exit
    code is 1, and the sink carries a governed app.unhandled_error ERROR
    event with the exception reduced to type plus location.
    """
    log_dir = tmp_path / "logs"

    def boom(raw_store_path: Path, document_id: str) -> str:
        raise RuntimeError("secret detail that must not reach stderr or sink")

    monkeypatch.setattr(cli, "load_raw_document", boom)

    exit_code = main(
        [
            "--log-dir",
            str(log_dir),
            "show-document",
            str(uuid.uuid4()),
            "--raw-store",
            str(tmp_path / "raw"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.strip().splitlines() == ["unexpected error: RuntimeError"]
    assert "Traceback" not in captured.err
    assert "secret detail" not in captured.err
    assert captured.out == ""

    errors = [
        line for line in _read_sink(log_dir) if line["event"] == CLI_UNHANDLED_ERROR
    ]
    assert len(errors) == 1
    assert errors[0]["level"] == "error"
    assert errors[0]["exc_type"] == "RuntimeError"
    assert "secret detail" not in json.dumps(errors)


def test_cli_run_emits_startup_config_into_the_sink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After bootstrap the CLI records the active toolset, exactly once.

    Given a CLI run, when the sink is read afterwards, then it contains one
    registered startup_config event carrying the git sha, the package
    versions, the allowlist size, the tracing flag, and the log format.
    """
    monkeypatch.delenv("OBSERVABILITY_TRACING", raising=False)
    log_dir = tmp_path / "logs"

    main(
        [
            "--log-dir",
            str(log_dir),
            "--log-format",
            "json",
            "show-document",
            str(uuid.uuid4()),
            "--raw-store",
            str(tmp_path / "raw"),
        ]
    )

    startups = [line for line in _read_sink(log_dir) if line["event"] == STARTUP_CONFIG]
    assert len(startups) == 1
    record = startups[0]
    assert record["git_sha"]
    assert record["allowlist_size"] > 0
    assert record["tracing_enabled"] is False
    assert record["log_format"] == "json"
    assert "structlog" in record["package_versions"]
