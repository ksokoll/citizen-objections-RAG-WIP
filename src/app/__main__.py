"""CLI composition root: python -m app.

The one front door of the system and its first non-fixture wiring. The CLI
owns bootstrap: configure_logging is called explicitly with the log directory
resolved here at the entrypoint and passed as a parameter (security finding
5: the sink path is never read from the process environment at import time),
a bootstrap failure aborts startup with an actionable message and no
traceback, and after a successful bootstrap the registered startup_config
event records the active toolset: git_sha, model_id, package versions,
corpus_id, allowlist size, tracing flag, log format, and the resolved store
paths.

Persistent store paths (raw store, audit log, log sink) have no CWD-relative
defaults (S5, Round 16.1): they default to locations under the app home
(--app-home, default ~/.citizen_objections) and every path, given or
defaulted, is resolved to an absolute path here at the entrypoint, so the
stores a run hits do not depend on the directory it was started from. The
resolved paths are recorded in startup_config.

The log format is a CLI decision only (--log-format, default json); there is
no environment fallback, console output is an explicit opt-in, and the
active format is recorded in startup_config (ADR-026).

Commands (ADR-028):

- process <path>: run the pipeline on one document and print the serialized
  WuerdigungsBriefing as JSON with ISO-8601 UTC datetimes. The briefing is
  the delivery contract; the CLI emits it and nothing prettier, because
  presentation happens in a frontend beyond the system boundary.
- show-document <id>: print the stored raw document for a document_id.
  An unknown or malformed id is a clear error and a nonzero exit.

Exit codes: 0 success, 1 run-level failure (unreadable document, unknown id,
pipeline error), 2 startup abort (logging bootstrap, missing configuration).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import structlog

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.briefing.serialization import to_json
from app.briefing.service import BriefingService
from app.core.failures import (
    IngestionError,
    RetrievalError,
    TriageError,
)
from app.core.protocols import LLMClientProtocol
from app.document_ingestion.protocols import PiiMasker
from app.document_ingestion.service import (
    MAX_RAW_TEXT_CHARS,
    DocumentIngestionService,
    load_raw_document,
)
from app.observability import (
    ObservabilityBootstrapError,
    ProcessorChainError,
    configure_logging,
)
from app.observability.events import CLI_UNHANDLED_ERROR, STARTUP_CONFIG
from app.observability.logging_config import ALLOWED_KEYS
from app.observability.tracing import tracing_enabled
from app.pipeline import Pipeline
from app.retrieval.gesetz_xml_loader import load_corpus
from app.retrieval.service import NormRetrievalService
from app.services.llm.mistral_client import MistralClient
from app.triage.service import TriageService

_log = structlog.get_logger()

#: The production Triage model (the only LLM call in the pipeline). Recorded
#: in startup_config so every run's output is attributable to its model.
TRIAGE_MODEL_ID: str = "mistral-large-latest"

#: Default app home for the persistent stores (raw store, audit log, log
#: sink): a fixed, absolute, user-owned location. The persistent stores must
#: never default to CWD-relative paths (S5): a run started from a different
#: directory would silently write a second raw store and a second audit
#: trail. Override with --app-home for tests and multi-instance setups.
DEFAULT_APP_HOME: Path = Path.home() / ".citizen_objections"

#: Packages whose versions shape the pipeline's output or its telemetry;
#: recorded in startup_config via importlib.metadata.
_PROVENANCE_PACKAGES: tuple[str, ...] = (
    "de_core_news_md",
    "mistralai",
    "opentelemetry-api",
    "opentelemetry-sdk",
    "presidio-analyzer",
    "presidio-anonymizer",
    "prometheus_client",
    "pydantic",
    "spacy",
    "structlog",
)


def _build_triage_llm() -> LLMClientProtocol:
    """Build the production Triage LLM client (the test seam).

    The medium-scale CLI smoke test monkeypatches this function with a fake,
    so the smoke exercises the full production wiring without a network call.

    Raises:
        KeyError: If MISTRAL_API_KEY is not set in the environment.
    """
    return MistralClient(model=TRIAGE_MODEL_ID)


def _build_masker() -> PiiMasker:
    """Build the production PII masker.

    Imported lazily, deliberately deviating from the module-level import
    rule: presidio_masker is the only module importing Presidio and spaCy,
    and the import chain alone is heavyweight. show-document must not pay it
    for a raw-store lookup, and the small-scale CLI tests stay model-free.
    """
    from app.document_ingestion.presidio_masker import PresidioMasker

    return PresidioMasker()


def _git_short_sha() -> str:
    """Return the current git short sha, or "unknown" outside a checkout.

    Best-effort and contained: provenance capture must never abort startup.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _package_versions() -> dict[str, str]:
    """Resolve the provenance package versions via importlib.metadata."""
    versions: dict[str, str] = {}
    for package in _PROVENANCE_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "unknown"
    return versions


def _emit_startup_config(
    log_format: str,
    paths: dict[str, Path],
    corpus_id: str | None = None,
    model_id: str | None = None,
) -> None:
    """Emit the registered startup_config event after bootstrap.

    Records the active toolset that produces this process's output. corpus_id
    and model_id apply only to commands that load the corpus and wire the
    LLM (process); show-document omits them rather than reporting stale or
    invented values. paths carries the resolved absolute store paths under
    their allowlisted keys (app_home, log_dir, raw_store, audit_log), so
    which stores a run actually hit is determinable afterward (S5).
    """
    fields: dict[str, object] = {
        "git_sha": _git_short_sha(),
        "package_versions": _package_versions(),
        "allowlist_size": len(ALLOWED_KEYS),
        "tracing_enabled": tracing_enabled(),
        "log_format": log_format,
    }
    for name, path in paths.items():
        fields[name] = str(path)
    if corpus_id is not None:
        fields["corpus_id"] = corpus_id
    if model_id is not None:
        fields["model_id"] = model_id
    _log.info(STARTUP_CONFIG, **fields)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser: global sink options plus the two commands.

    The sink options are accepted both before and after the subcommand. The
    main parser carries the real defaults; the per-command copies default to
    SUPPRESS so a value given before the subcommand is not overwritten by a
    subparser default. Store paths default to None here and are derived from
    the app home in _resolve_paths, so no persistent store ever has a
    CWD-relative default (S5).
    """
    sink_options = argparse.ArgumentParser(add_help=False)
    sink_options.add_argument(
        "--app-home",
        type=Path,
        default=argparse.SUPPRESS,
        help="base directory for the persistent stores (raw store, audit "
        "log, log sink)",
    )
    sink_options.add_argument(
        "--log-dir",
        type=Path,
        default=argparse.SUPPRESS,
        help="governed log sink directory (default: <app-home>/logs)",
    )
    sink_options.add_argument(
        "--log-format",
        choices=("json", "console"),
        default=argparse.SUPPRESS,
        help="log output format (default: json; console is a developer "
        "opt-in, ADR-026)",
    )

    parser = argparse.ArgumentParser(
        prog="python -m app",
        description=(
            "Citizen-objections pipeline. Processes one Einwendung into a "
            "WuerdigungsBriefing (serialized JSON, ADR-028) or looks up a "
            "stored raw document."
        ),
    )
    parser.add_argument(
        "--app-home",
        type=Path,
        default=DEFAULT_APP_HOME,
        help="base directory for the persistent stores (raw store, audit "
        f"log, log sink); default: {DEFAULT_APP_HOME}",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="governed log sink directory (default: <app-home>/logs)",
    )
    parser.add_argument(
        "--log-format",
        choices=("json", "console"),
        default="json",
        help="log output format (default: json; console is a developer "
        "opt-in, ADR-026)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    process = commands.add_parser(
        "process",
        help="run the pipeline on one document, print the briefing JSON",
        parents=[sink_options],
    )
    process.add_argument("document", type=Path, help="path to the raw Einwendung text")
    process.add_argument(
        "--xml-dir",
        type=Path,
        default=Path("data") / "XML",
        help="statute XML corpus directory (read-only input)",
    )
    process.add_argument(
        "--raw-store",
        type=Path,
        default=None,
        help="raw document store directory (default: <app-home>/raw_store)",
    )
    process.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="append-only audit log file (default: <app-home>/audit.jsonl)",
    )

    show = commands.add_parser(
        "show-document",
        help="print the stored raw document for a document id",
        parents=[sink_options],
    )
    show.add_argument("document_id", help="ingestion-assigned document id")
    show.add_argument(
        "--raw-store",
        type=Path,
        default=None,
        help="raw document store directory (default: <app-home>/raw_store)",
    )
    return parser


def _resolve_paths(args: argparse.Namespace) -> dict[str, Path]:
    """Resolve every persistent store path to an absolute location.

    A given path is resolved against the CWD once, here at the entrypoint; a
    defaulted path is derived from the app home. Either way the paths the
    process works with are absolute from this point on, so a later relative
    CWD change cannot redirect which stores are hit (S5). The resolved map
    feeds startup_config.

    Args:
        args: The parsed CLI namespace; its path attributes are replaced by
            their resolved absolute values.

    Returns:
        The resolved paths keyed by their startup_config field names.
    """
    app_home = args.app_home.resolve()
    args.app_home = app_home
    args.log_dir = (
        args.log_dir.resolve() if args.log_dir is not None else app_home / "logs"
    )
    resolved: dict[str, Path] = {"app_home": app_home, "log_dir": args.log_dir}
    if hasattr(args, "raw_store"):
        args.raw_store = (
            args.raw_store.resolve()
            if args.raw_store is not None
            else app_home / "raw_store"
        )
        resolved["raw_store"] = args.raw_store
    if hasattr(args, "audit_log"):
        args.audit_log = (
            args.audit_log.resolve()
            if args.audit_log is not None
            else app_home / "audit.jsonl"
        )
        resolved["audit_log"] = args.audit_log
    return resolved


def _run_process(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    """Wire the production pipeline and process one document."""
    # Size guard before read (S5): the ingestion limit is enforced on the
    # file size via stat, so an oversized document is refused before its
    # content is ever loaded into the process. UTF-8 stores every character
    # in at least one byte, so a file within the byte bound is also within
    # the character bound the service enforces; a multibyte-heavy file near
    # the limit may be refused here that the service would accept, which is
    # the conservative side of a pre-read guard.
    try:
        document_size = args.document.stat().st_size
    except OSError as exc:
        print(f"could not read document '{args.document}': {exc}", file=sys.stderr)
        return 1
    if document_size > MAX_RAW_TEXT_CHARS:
        print(
            f"document '{args.document}' exceeds the {MAX_RAW_TEXT_CHARS}-character "
            f"ingestion limit ({document_size} bytes); reject at the boundary "
            "rather than drive the masker unboundedly",
            file=sys.stderr,
        )
        return 1

    try:
        raw_text = args.document.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"could not read document '{args.document}': {exc}", file=sys.stderr)
        return 1

    try:
        llm = _build_triage_llm()
    except KeyError:
        print(
            "startup aborted: MISTRAL_API_KEY is not set in the environment",
            file=sys.stderr,
        )
        return 2
    try:
        corpus = load_corpus(args.xml_dir)
    except FileNotFoundError as exc:
        print(f"startup aborted: {exc}", file=sys.stderr)
        return 2
    retrieval = NormRetrievalService(corpus)
    _emit_startup_config(
        log_format=args.log_format,
        paths=paths,
        corpus_id=retrieval.corpus_id,
        model_id=TRIAGE_MODEL_ID,
    )

    pipeline = Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=args.raw_store,
            masker=_build_masker(),
        ),
        triage=TriageService(llm=llm),
        retrieval=retrieval,
        briefing=BriefingService(),
        audit=AuditLogService(store=JsonLinesAuditStore(args.audit_log)),
    )

    try:
        briefing = pipeline.run(raw_text)
    except (IngestionError, TriageError, RetrievalError) as exc:
        print(f"processing failed: {exc}", file=sys.stderr)
        return 1

    print(to_json(briefing))
    return 0


def _run_show_document(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    """Look up one stored raw document by id."""
    _emit_startup_config(log_format=args.log_format, paths=paths)
    try:
        raw_text = load_raw_document(args.raw_store, args.document_id)
    except IngestionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(raw_text)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: bootstrap strictly, then dispatch the command.

    Bootstrap precedes everything: no pipeline wiring and no context code
    runs before the governed sink is installed, so no log can escape the
    default-deny chain (ADR-026, phase separation).

    Args:
        argv: Command line arguments; defaults to sys.argv.

    Returns:
        The process exit code (0 success, 1 run failure, 2 startup abort).
    """
    args = _build_parser().parse_args(argv)
    paths = _resolve_paths(args)

    try:
        configure_logging(log_dir=args.log_dir, fmt=args.log_format)
    except (ObservabilityBootstrapError, ProcessorChainError) as exc:
        print(f"startup aborted: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "process":
            return _run_process(args, paths)
        return _run_show_document(args, paths)
    except Exception as exc:
        # Dispatch catch-all (S1/M4): an unexpected exception becomes a
        # governed ERROR event (the chain reduces it to type plus location)
        # and one clean stderr line carrying the type only. The exception
        # message is foreign-authored text and never reaches stderr or the
        # sink; a traceback would be the richest leak channel of all.
        _log.error(CLI_UNHANDLED_ERROR, exc_info=True)
        print(f"unexpected error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
