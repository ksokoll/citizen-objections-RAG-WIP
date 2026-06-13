"""Import-isolation test: context services stay telemetry-free (H1).

Every context service imports app.observability.tracing for the @traced
decorator. Before this round that pulled the OpenTelemetry and Prometheus
stacks into the context import graph at module load. Now both are imported
lazily, so importing a context service must not import opentelemetry or
prometheus_client.

The check runs in a fresh subprocess: the pytest process itself has already
imported both stacks (the Coordinator and the metrics tests use them), so
sys.modules in-process cannot tell whether importing the context pulled them.
A clean interpreter that imports only the context service and then inspects
sys.modules is the hermetic way to assert the import graph.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

#: The context services whose import graph must stay free of the telemetry
#: stack. Each imports app.observability.tracing for @traced.
_CONTEXT_SERVICE_MODULES = (
    "app.triage.service",
    "app.retrieval.service",
    "app.briefing.service",
    "app.audit_log.service",
    "app.document_ingestion.service",
)

#: The telemetry packages that must not be pulled in by a context-service import.
_FORBIDDEN_TELEMETRY = ("opentelemetry", "prometheus_client")


@pytest.mark.parametrize("module_name", _CONTEXT_SERVICE_MODULES)
def test_importing_a_context_service_does_not_import_telemetry(
    module_name: str,
) -> None:
    # Given: a fresh interpreter that imports only the one context service
    program = textwrap.dedent(
        f"""
        import importlib
        import sys

        importlib.import_module({module_name!r})

        forbidden = {_FORBIDDEN_TELEMETRY!r}
        leaked = sorted(
            name
            for name in sys.modules
            if any(name == pkg or name.startswith(pkg + ".") for pkg in forbidden)
        )
        if leaked:
            print(",".join(leaked))
            raise SystemExit(1)
        raise SystemExit(0)
        """
    )

    # When: it runs and inspects its own sys.modules
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Then: neither opentelemetry nor prometheus_client was imported, so the
    # @traced decorator did not pull the telemetry stack into the context graph.
    assert result.returncode == 0, (
        f"{module_name} pulled telemetry packages into its import graph: "
        f"{result.stdout.strip()}"
    )
