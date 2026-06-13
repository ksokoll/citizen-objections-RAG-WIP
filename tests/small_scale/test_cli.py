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
from app.document_ingestion.events import RAW_DOCUMENT_ACCESSED
from app.document_ingestion.service import (
    MAX_RAW_TEXT_CHARS,
    DocumentIngestionService,
)
from app.observability.logging_config import LOG_FILENAME
from app.observability_registry import CLI_UNHANDLED_ERROR, STARTUP_CONFIG
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


def test_show_document_leaves_exactly_one_access_trace_without_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reading raw PII back out leaves a governed trace, id only (H4/S4).

    Given a stored document, when show-document reads it, then the sink
    carries exactly one raw_document_accessed event with the document_id and
    the document's content appears nowhere in the sink.
    """
    raw_store = tmp_path / "raw"
    ingestion = DocumentIngestionService(
        raw_store_path=raw_store,
        masker=FakePiiMasker(),
    )
    stored = ingestion.ingest(_ORIGINAL_TEXT)
    log_dir = tmp_path / "logs"

    exit_code = main(
        [
            "--log-dir",
            str(log_dir),
            "show-document",
            stored.document_id,
            "--raw-store",
            str(raw_store),
        ]
    )

    capsys.readouterr()
    assert exit_code == 0
    lines = _read_sink(log_dir)
    accesses = [line for line in lines if line["event"] == RAW_DOCUMENT_ACCESSED]
    assert len(accesses) == 1
    assert accesses[0]["document_id"] == stored.document_id
    assert _ORIGINAL_TEXT not in json.dumps(lines)


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


def test_cli_hits_the_same_absolute_stores_from_any_working_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Store paths derive from the app home, not from the CWD (S5).

    Given a document stored under an app home's raw store, when show-document
    runs with that app home from two different working directories and no
    explicit store path, then both runs find the document in the same
    absolute store and startup_config records the resolved absolute paths.
    """
    app_home = tmp_path / "home"
    ingestion = DocumentIngestionService(
        raw_store_path=app_home / "raw_store",
        masker=FakePiiMasker(),
    )
    stored = ingestion.ingest(_ORIGINAL_TEXT)
    cwd_one = tmp_path / "cwd_one"
    cwd_two = tmp_path / "cwd_two"
    cwd_one.mkdir()
    cwd_two.mkdir()

    exit_codes = []
    for cwd in (cwd_one, cwd_two):
        monkeypatch.chdir(cwd)
        exit_codes.append(
            main(["--app-home", str(app_home), "show-document", stored.document_id])
        )

    captured = capsys.readouterr()
    assert exit_codes == [0, 0]
    assert captured.out.count(_ORIGINAL_TEXT) == 2
    assert not (cwd_one / "raw_store").exists()
    assert not (cwd_two / "raw_store").exists()

    startups = [
        line
        for line in _read_sink(app_home / "logs")
        if line["event"] == STARTUP_CONFIG
    ]
    assert len(startups) == 2
    for record in startups:
        assert Path(record["raw_store"]) == (app_home / "raw_store").resolve()
        assert Path(record["raw_store"]).is_absolute()
        assert Path(record["log_dir"]).is_absolute()
        assert Path(record["app_home"]).is_absolute()


def test_oversized_document_is_refused_before_read(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file over the ingestion limit is refused via stat, before read (S5).

    Given a document file larger than the ingestion limit, when process runs,
    then it exits 1 with the boundary message before any corpus, masker, or
    LLM wiring happens (the refusal needs no MISTRAL_API_KEY and no statute
    corpus, which is itself the evidence that nothing was wired).
    """
    oversized = tmp_path / "einwendung.txt"
    oversized.write_text("a" * (MAX_RAW_TEXT_CHARS + 1), encoding="utf-8")

    exit_code = main(
        [
            "--app-home",
            str(tmp_path / "home"),
            "process",
            str(oversized),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"{MAX_RAW_TEXT_CHARS}-character" in captured.err
    assert captured.out == ""


def test_observability_format_env_does_not_change_the_renderer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The log format is a CLI decision only; the env fallback is gone (S7).

    Given OBSERVABILITY_FORMAT=console in the process environment, when the
    CLI runs without a --log-format flag, then the sink still renders JSON
    and startup_config records the json format: the renderer of the governed
    sink cannot be switched through the environment (ADR-026).
    """
    monkeypatch.setenv("OBSERVABILITY_FORMAT", "console")
    app_home = tmp_path / "home"

    main(
        [
            "--app-home",
            str(app_home),
            "show-document",
            str(uuid.uuid4()),
        ]
    )

    lines = _read_sink(app_home / "logs")
    assert lines, "the sink must parse as JSON lines (json renderer active)"
    startups = [line for line in lines if line["event"] == STARTUP_CONFIG]
    assert len(startups) == 1
    assert startups[0]["log_format"] == "json"


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
