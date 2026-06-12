# citizen-objections-RAG

A RAG system that processes German public-participation objections
(Masseneinwendungen) for a Behörde. Raw Einwendung text in, a
Würdigungs-Briefing for the Sachbearbeiter out.

Five bounded contexts run as a sequential pipeline:
DocumentIngestion -> Triage -> Retrieval -> Briefing -> AuditLog.
Architecture and per-context contracts are documented in
`docs/BOUNDED_CONTEXTS.md`; decisions live in `docs/decisions/` (MADR).

## Scope

The system boundary is the structured `WuerdigungsBriefing`: the pipeline
delivers it as a serialized JSON contract, and everything human-readable
happens beyond the boundary, in a frontend outside this repository
(ADR-028). The CLI (`python -m app process <path>`) therefore emits the
serialized briefing and nothing prettier.

The case-specific facts of the building project (the Akte) are outside the
system boundary as well: the briefing supports the Sachbearbeiter's
Abwägung, it does not perform it (ADR-022).

## Run

Windows PowerShell, virtualenv at `.\venv\Scripts\python.exe`.

- All tests: `.\venv\Scripts\python.exe -m pytest -q`
- Process one document: `.\venv\Scripts\python.exe -m app process <path>`
- Inspect a stored raw document: `.\venv\Scripts\python.exe -m app show-document <id>`
