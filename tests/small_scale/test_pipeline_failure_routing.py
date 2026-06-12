"""Behaviour tests for the pipeline's failure routing at the Triage boundary.

The S1 reproduction (Round 16.1): an LLM seam failure, here an invalid
catalog_id that fails schema validation exactly as the real client validates,
must end as a complete custody trail (PIPELINE_FEHLER recorded) plus the
terminal-status metric, not as an uncaught infrastructure exception tearing a
gap into the trail. The boundary set in the Coordinator stays unchanged
because TriageService translates LLMError into TriageError (S1, M4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.audit_log.service import AuditLogService
from app.audit_log.store import JsonLinesAuditStore
from app.briefing.service import BriefingService
from app.core.events import AuditEventType
from app.core.failures import LLMError, TriageError
from app.document_ingestion.service import DocumentIngestionService
from app.observability import metrics
from app.pipeline import Pipeline
from app.triage.service import TriageService
from tests.conftest import FakePiiMasker, FakeRetriever

_SAMPLE_EINWENDUNG = (
    "Ein vorhabenbezogener Bebauungsplan, der von dieser "
    "Darstellung des Flächennutzungsplans abweicht."
)

#: A provider response whose catalog_id is not in the CatalogId enum. The
#: seam-faithful client below validates it against the schema and fails,
#: exactly like the production client does post-hoc.
_INVALID_CATALOG_RESPONSE: dict[str, Any] = {
    "argumente": [
        {
            "argument_text": "Verstoß gegen das Strafgesetzbuch",
            "original_zitat": "Ein vorhabenbezogener Bebauungsplan",
            "catalog_id": "stgb",
            "einwendungs_typ": "TYP_2",
        }
    ]
}


class SeamFaithfulLLMClient:
    """LLMClientProtocol double replaying a raw provider JSON through the seam.

    Mirrors the documented client contract (services/llm/mistral_client.py):
    the raw response is validated against the Pydantic schema after the call,
    and a validation failure is translated into LLMError before it leaves the
    client. Replaying a prepared raw dict through this double exercises the
    same code path as a real provider returning out-of-schema content.
    """

    def __init__(self, raw_response: dict[str, Any]) -> None:
        self._raw_response = raw_response

    def generate(self, prompt: str, system_prompt: str = "") -> str:
        return ""

    def parse(
        self,
        prompt: str,
        response_format: type[BaseModel],
        system_prompt: str = "",
    ) -> BaseModel:
        try:
            return response_format.model_validate(self._raw_response)
        except Exception as exc:
            raise LLMError(f"JSON failed schema validation: {exc}") from exc


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    """Read one sample from the in-process registry, defaulting to 0."""
    return metrics.REGISTRY.get_sample_value(name, labels) or 0


def test_invalid_catalog_id_from_the_llm_seam_ends_as_pipeline_fehler(
    tmp_path: Path,
) -> None:
    """The S1 reproduction: a seam failure leaves a complete custody trail.

    Given a triage LLM whose response carries an invalid catalog_id (failing
    schema validation in the seam, raised as LLMError), when the pipeline
    runs, then TriageError leaves run() (LLMError no longer reaches the
    Coordinator's boundary set), the audit log ends with PIPELINE_FEHLER for
    the run's document, and objections_processed{pipeline_fehler} grows by
    one. No reproducible completeness gap, no ungoverned stderr.
    """
    audit_store = JsonLinesAuditStore(tmp_path / "audit.jsonl")
    pipeline = Pipeline(
        ingestion=DocumentIngestionService(
            raw_store_path=tmp_path / "raw",
            masker=FakePiiMasker(),
        ),
        triage=TriageService(llm=SeamFaithfulLLMClient(_INVALID_CATALOG_RESPONSE)),
        retrieval=FakeRetriever(corpus_id="corpus-id-failure-routing-test"),
        briefing=BriefingService(),
        audit=AuditLogService(store=audit_store),
    )
    label = {"status": AuditEventType.PIPELINE_FEHLER.value}
    failures_before = _sample("objections_processed_total", label)

    with pytest.raises(TriageError):
        pipeline.run(_SAMPLE_EINWENDUNG)

    events = audit_store.query()
    assert events[-1].event_type == AuditEventType.PIPELINE_FEHLER
    assert _sample("objections_processed_total", label) - failures_before == 1
