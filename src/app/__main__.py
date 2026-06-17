"""CLI composition root: python -m app.

The one front door of the system and its first non-fixture wiring. The CLI
owns bootstrap: configure_logging is called explicitly with the log directory
resolved here at the entrypoint and passed as a parameter (security finding
5: the sink path is never read from the process environment at import time),
a missing default-deny allowlist aborts startup cleanly with no traceback
(ProcessorChainError) while any other configure failure propagates the
underlying error, and after a successful bootstrap the registered startup_config
event records the active toolset: git_sha, model_id, package versions,
corpus_id, allowlist size, tracing flag, log format, and the resolved store
paths. For the process command the same content-free provenance (no paths) is
additionally written into the tamper-evident chain as a STARTKONFIGURATION
event under the SYSTEM sentinel, so the controls' activity is provable after
the fact and not only in the retention-bound log (ADR-031).

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
- show-document <id>: print the stored raw document for a document_id. The
  read of unmasked PII is recorded as a ROHDOKUMENT_ZUGRIFF event in the
  tamper-evident chain before the content is printed, fail-closed: if that
  custody write fails, the read aborts and nothing is printed (ADR-033). An
  unknown or malformed id is a clear error and a nonzero exit.
- verify-audit: fully walk the audit hash chain and report the first break
  for an auditor (ADR-031). Non-mutating (it never opens the store), so an
  audit cannot trigger recovery; a detected break is a nonzero exit with a
  located, content-free report.

Exit codes: 0 success, 1 run-level failure (unreadable document, unknown id,
pipeline error, a detected chain break), 2 startup abort (logging bootstrap,
missing configuration).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import structlog

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore, verify_chain_file
from app.briefing.serialization import to_json
from app.briefing.service import BriefingService
from app.core.failures import (
    AuditLogError,
    IngestionError,
    RetrievalError,
    TriageError,
)
from app.document_ingestion.protocols import PiiMasker
from app.document_ingestion.service import (
    MAX_RAW_TEXT_CHARS,
    DocumentIngestionService,
    load_raw_document,
)
from app.observability import (
    ProcessorChainError,
    configure_logging,
)
from app.observability.logging_config import ENV_STRICT, allowed_keys
from app.observability.tracing import ENV_TRACING, set_tracing_enabled, tracing_enabled
from app.observability_registry import (
    CLI_UNHANDLED_ERROR,
    STARTUP_CONFIG,
    register_observability_vocabulary,
)
from app.pipeline import Pipeline
from app.retrieval.gesetz_xml_loader import load_corpus
from app.retrieval.service import NormRetrievalService
from app.services.llm.mistral_client import (
    EndpointNotAllowedError,
    MistralClient,
    check_endpoint_allowed,
)
from app.triage.protocols import LLMClientProtocol
from app.triage.service import TriageService

_log = structlog.get_logger()

#: The production Triage model (the only LLM call in the pipeline). Recorded
#: in startup_config so every run's output is attributable to its model.
TRIAGE_MODEL_ID: str = "mistral-large-latest"

#: The public Mistral cloud endpoint, the SDK's own default. The wired default
#: so the demo runs unconfigured (K1); a Behörde overrides --mistral-endpoint
#: with its encapsulated endpoint and narrows the allowlist to exclude this one.
DEFAULT_MISTRAL_ENDPOINT: str = "https://api.mistral.ai"

#: The endpoints the resolved Triage endpoint is checked against at startup (K1,
#: ADR-027). The default admits the public cloud so the unconfigured demo runs;
#: a Behörde narrows it via --mistral-endpoint-allowlist to its encapsulated
#: endpoint and excludes the public cloud. An off-list endpoint is a fail-loud
#: startup abort, not a silent outbound call to an unvetted destination. This
#: checked fact is what the narrow PII-masking scope now rests on (ADR-025).
DEFAULT_ENDPOINT_ALLOWLIST: tuple[str, ...] = (DEFAULT_MISTRAL_ENDPOINT,)

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


def _build_triage_llm(base_url: str) -> LLMClientProtocol:
    """Build the production Triage LLM client (the test seam).

    The medium-scale CLI smoke test monkeypatches this function with a fake,
    so the smoke exercises the full production wiring without a network call.

    Args:
        base_url: The endpoint to reach, already checked against the allowlist
            at startup (check_endpoint_allowed); the client only transports it.

    Raises:
        KeyError: If MISTRAL_API_KEY is not set in the environment.
    """
    return MistralClient(model=TRIAGE_MODEL_ID, base_url=base_url)


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


def _startup_config_provenance(
    log_format: str,
    corpus_id: str | None = None,
    model_id: str | None = None,
    endpoint: str | None = None,
) -> dict[str, object]:
    """Build the content-free provenance of the active controls.

    The fields that prove, after the fact, which toolset produced this run's
    output: the git sha, the package versions, the allowlist size, the tracing
    flag, the log format, and (for the process command) the corpus hash, the
    model id, and the admitted endpoint. Deliberately no store paths: a path can
    exceed the payload length bound and is not part of the proof, so it stays in
    the log event (S5) and out of the content-free chain event (ADR-031). Every
    value here is a simple type or a flat version map, so it satisfies the
    AuditEvent payload allowlist.
    """
    provenance: dict[str, object] = {
        "git_sha": _git_short_sha(),
        "package_versions": _package_versions(),
        "allowlist_size": len(allowed_keys()),
        "tracing_enabled": tracing_enabled(),
        "log_format": log_format,
    }
    if corpus_id is not None:
        provenance["corpus_id"] = corpus_id
    if model_id is not None:
        provenance["model_id"] = model_id
    if endpoint is not None:
        provenance["mistral_endpoint"] = endpoint
    return provenance


def _emit_startup_config(
    log_format: str,
    paths: dict[str, Path],
    *,
    corpus_id: str | None = None,
    model_id: str | None = None,
    endpoint: str | None = None,
) -> dict[str, object]:
    """Log the active toolset and return its content-free provenance.

    Records the active toolset that produces this process's output as a governed
    log event. corpus_id, model_id, and endpoint apply only to commands that load
    the corpus and wire the LLM (process); show-document omits them rather than
    reporting stale or invented values. The endpoint is the destination the
    startup allowlist check admitted (K1), so a run's output is attributable to
    the endpoint it actually reached.

    The log event carries the resolved absolute store paths under their
    allowlisted keys (app_home, log_dir, raw_store, audit_log), so which stores a
    run actually hit is determinable afterward (S5).

    Returns the content-free provenance (no paths) so the process command can
    additionally record it into the tamper-evident chain via
    AuditLogService.record_startup_config (ADR-031, A3): the audit context owns
    the STARTKONFIGURATION custody event, the CLI passes provenance only. Paths
    stay out of the returned provenance: not part of the proof, and a path can
    exceed the payload bound.
    """
    provenance = _startup_config_provenance(log_format, corpus_id, model_id, endpoint)

    log_fields = dict(provenance)
    for name, path in paths.items():
        log_fields[name] = str(path)
    _log.info(STARTUP_CONFIG, **log_fields)
    return provenance


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
        "--mistral-endpoint",
        default=DEFAULT_MISTRAL_ENDPOINT,
        help="Triage LLM endpoint; checked against the allowlist at startup "
        f"(default: {DEFAULT_MISTRAL_ENDPOINT})",
    )
    process.add_argument(
        "--mistral-endpoint-allowlist",
        default=None,
        help="comma-separated endpoints the resolved endpoint must be on; a "
        "Behörde narrows this to its encapsulated endpoint (default: the "
        "public Mistral cloud, so the demo runs unconfigured)",
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
    show.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="append-only audit log file the read-access event is written to "
        "(default: <app-home>/audit.jsonl)",
    )

    verify = commands.add_parser(
        "verify-audit",
        help="fully verify the audit hash chain; exit nonzero on a break",
        parents=[sink_options],
    )
    verify.add_argument(
        "--audit-log",
        type=Path,
        default=None,
        help="append-only audit log file to verify (default: <app-home>/audit.jsonl)",
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

    # Endpoint allowlist (K1): resolve the endpoint and the allowlist, then check
    # the destination before any client is built. An off-list endpoint is a
    # fail-loud startup abort (exit 2, like the other bootstrap aborts), so
    # pseudonymized text is never sent to an unvetted destination by accident.
    # This checked fact is what the narrow PII-masking scope now rests on.
    if args.mistral_endpoint_allowlist is None:
        allowlist = DEFAULT_ENDPOINT_ALLOWLIST
    else:
        allowlist = tuple(
            entry.strip()
            for entry in args.mistral_endpoint_allowlist.split(",")
            if entry.strip()
        )
    try:
        endpoint = check_endpoint_allowed(args.mistral_endpoint, allowlist)
    except EndpointNotAllowedError as exc:
        print(f"startup aborted: {exc}", file=sys.stderr)
        return 2

    try:
        llm = _build_triage_llm(base_url=endpoint)
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
    # The audit store is opened before the startup_config is recorded so the
    # configuration custody event becomes the chain's genesis: the controls are
    # attested before any objection event is appended (ADR-031). This is the
    # writing path, so it is composed through open_for_writing, the one factory
    # that seeds the head (seed_head) then runs the fast tail-window check
    # (verify_open) in order before the chain continues. A damaged or tampered
    # tail aborts the run here, loudly. The bare constructor stays the read path
    # (A5).
    audit_store = JsonLinesAuditStore.open_for_writing(args.audit_log)
    audit_service = AuditLogService(store=audit_store)
    provenance = _emit_startup_config(
        log_format=args.log_format,
        paths=paths,
        # The startup_config corpus_id field keeps its name (provenance of the
        # statute corpus, ADR-026); its value is the retriever's
        # source_revision, which for this corpus-based retriever is the corpus
        # hash (ADR-028, M2).
        corpus_id=retrieval.source_revision,
        model_id=TRIAGE_MODEL_ID,
        # The endpoint the allowlist check admitted: the destination this run's
        # output is attributable to (K1, ADR-027).
        endpoint=endpoint,
    )
    # The process command wires the audit store, so the same content-free
    # provenance is additionally written into the chain (ADR-031). The audit
    # context's service constructs and publishes the STARTKONFIGURATION custody
    # event; the CLI passes provenance only, no audit-schema knowledge in the
    # wiring layer (A3).
    audit_service.record_startup_config(provenance)

    pipeline = Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=args.raw_store,
            masker=_build_masker(),
        ),
        triage=TriageService(llm=llm),
        retrieval=retrieval,
        briefing=BriefingService(),
        audit=audit_service,
    )

    try:
        briefing = pipeline.run(raw_text)
    except (IngestionError, TriageError, RetrievalError) as exc:
        print(f"processing failed: {exc}", file=sys.stderr)
        return 1

    print(to_json(briefing))
    return 0


def _run_show_document(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    """Look up one stored raw document by id, recording the access fail-closed.

    Reading raw PII back out is the one custody-relevant read path (ADR-033).
    The document is loaded first, which validates the id and emits the
    operational raw_document_accessed log event (ADR-027). Then the read-access
    event is written into the tamper-evident chain, and the content is printed
    only if that write is durable: raw PII is disclosed only when the access is
    provably recorded in the immutable chain, not merely in the deletable log. A
    failure anywhere in the audit path (lock contention, a tampered tail caught
    at open, the append itself) aborts the read with a nonzero exit and prints
    nothing.

    The audit store is opened as a writing path via open_for_writing (seed_head
    then verify_open, like process): seed_head seeds the chain head so the read
    event chains onto the real tail rather than re-seeding genesis, and
    verify_open refuses to disclose PII onto a chain whose tail does not verify.
    """
    _emit_startup_config(log_format=args.log_format, paths=paths)
    try:
        raw_text = load_raw_document(args.raw_store, args.document_id)
    except IngestionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        audit_store = JsonLinesAuditStore.open_for_writing(args.audit_log)
        AuditLogService(store=audit_store).record_raw_document_read(args.document_id)
    except AuditLogError as exc:
        print(
            f"raw-document access could not be recorded in the audit chain, "
            f"refusing to disclose the document: {exc}",
            file=sys.stderr,
        )
        return 1
    print(raw_text)
    return 0


def _run_verify_audit(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    """Fully verify the audit hash chain for an auditor (ADR-031).

    Reads and walks the whole chain from genesis (verify_chain_file), which is
    non-mutating: it never opens the store, so an audit run cannot trigger
    recovery or append a recovery event. A clean chain exits 0; a detected break
    exits 1 with a content-free, located description on stderr, so an automated
    audit can gate on the exit code and a human reads where the chain broke.
    """
    _emit_startup_config(log_format=args.log_format, paths=paths)
    if not args.audit_log.exists():
        print(f"no audit chain at {args.audit_log}; nothing to verify")
        return 0
    result = verify_chain_file(args.audit_log)
    if result.ok:
        print(f"audit chain verified: {args.audit_log}")
        return 0
    # Invariant (verification.py): first_break is None exactly when ok is True,
    # so a non-ok result always carries a break to describe.
    assert result.first_break is not None
    print(
        f"audit chain verification FAILED for {args.audit_log}: "
        f"{result.first_break.describe()}",
        file=sys.stderr,
    )
    return 1


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

    # Assemble the event vocabulary the logging chain enforces against, before
    # any event can flow: observability holds the mechanism, the root unions in
    # each context's declared events plus the CLI's own (H2). Idempotent.
    register_observability_vocabulary()

    # Behavior flags are resolved once here at the root and wired in, never read
    # live from the environment deep in the observability stack (ADR-026,
    # composition-root wiring). Strict mode is forwarded to configure_logging;
    # tracing is wired so the @traced decorator and the startup_config record
    # see the same value.
    strict = os.environ.get(ENV_STRICT) == "1"
    set_tracing_enabled(os.environ.get(ENV_TRACING) == "1")

    try:
        configure_logging(log_dir=args.log_dir, fmt=args.log_format, strict=strict)
    except ProcessorChainError as exc:
        print(f"startup aborted: {exc}", file=sys.stderr)
        return 2

    try:
        if args.command == "process":
            return _run_process(args, paths)
        if args.command == "verify-audit":
            return _run_verify_audit(args, paths)
        return _run_show_document(args, paths)
    except Exception as exc:
        # Dispatch catch-all (ADR-026, exception policy): an unexpected
        # exception becomes a governed ERROR event (the chain reduces it to type
        # plus location) and one clean stderr line carrying the type only. The
        # exception message is foreign-authored text and never reaches stderr or
        # the sink; a traceback would be the richest leak channel of all.
        _log.error(CLI_UNHANDLED_ERROR, exc_info=True)
        print(f"unexpected error: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
