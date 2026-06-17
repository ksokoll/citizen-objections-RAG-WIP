"""Log event vocabulary owned by the DocumentIngestion context.

Each context declares the event constants it emits, rather than a central
observability registry naming foreign owners (H2). The composition root unions
these per-context declarations into the registry the logging chain enforces
against, so observability keeps the mechanism while domain vocabulary lives
with the context that emits it (ADR-026).
"""

from __future__ import annotations

from typing import Final

#: A persisted raw store is world-accessible on POSIX. A misconfiguration, not
#: a masking outcome: logged, processing continues.
INGESTION_RAW_STORE_WORLD_READABLE: Final[str] = "ingestion.raw_store_world_readable"

#: Deterministic anchor name tokens survived masking in their own zone. An
#: internal contradiction; logged as a count only, never the surviving tokens,
#: so the anomaly signal carries no PII.
INGESTION_PII_COVERAGE_ANOMALY: Final[str] = "ingestion.pii_coverage_anomaly"

#: A stored raw document (unmasked PII) was read back out of the raw store.
#: Emitted on every successful load_raw_document call with the document_id
#: only, never content: the read path on raw PII leaves an operational trace.
RAW_DOCUMENT_ACCESSED: Final[str] = "ingestion.raw_document_accessed"

#: Event constants this context emits, unioned into the registry at the
#: composition root.
INGESTION_EVENTS: Final[frozenset[str]] = frozenset(
    {
        INGESTION_RAW_STORE_WORLD_READABLE,
        INGESTION_PII_COVERAGE_ANOMALY,
        RAW_DOCUMENT_ACCESSED,
    }
)

#: Allowlisted log field names this context emits, unioned into ALLOWED_KEYS at
#: the composition root (ADR-026, default-deny). Counts and a mode string only,
#: never PII: survivor_count and name_regions_masked are counts on the coverage
#: anomaly (never the surviving tokens), store_mode is the octal mode on the
#: world-readable check, and document_id is the pseudonymous id on the
#: raw-document access trace (never content).
INGESTION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "survivor_count",
        "name_regions_masked",
        "store_mode",
        "document_id",
    }
)
