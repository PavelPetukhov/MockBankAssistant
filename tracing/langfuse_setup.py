"""Langfuse + OpenInference instrumentation setup for ADK traces.

How the pieces fit:
  - OpenInference's GoogleADKInstrumentor turns ADK runs into OpenTelemetry spans
    (agent_run -> call_llm -> execute_tool ...).
  - The Langfuse client installs an OTel span processor that exports those spans to
    LANGFUSE_HOST (local docker stack or Langfuse Cloud).
  - Per-turn trace attributes (session, scenario/run id, flag state) are attached with
    langfuse.propagate_attributes() in agent/session.py.

If Langfuse keys are missing, setup_tracing() returns False and the agent runs untraced.
"""

import logging

import config

log = logging.getLogger(__name__)

_initialised = False


def _should_export(span) -> bool:
    # VERIFY (langfuse 4.x): the default filter also exports ADK's native spans (they carry
    # gen_ai.* attributes), which duplicates every OpenInference span in the trace tree.
    # Keep only Langfuse SDK spans and OpenInference spans.
    from langfuse.span_filter import is_langfuse_span

    scope = span.instrumentation_scope.name if span.instrumentation_scope else ""
    return is_langfuse_span(span) or scope == "openinference" or scope.startswith("openinference.")


class _DropTransferDetachNoise(logging.Filter):
    """Drop OTel's "Failed to detach context" error on agent transfers.

    VERIFY (openinference-instrumentation-google-adk 1.0.4 + google-adk 2.11): after
    transfer_to_agent, ADK abandons the root agent's async generator; it is closed later in
    another context and OpenInference's agent_run wrapper can't detach its OTel context.
    The spans still end and export correctly, so this is log noise only. Remove this filter
    once upstream fixes it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage() != "Failed to detach context"


def setup_tracing() -> bool:
    """Initialise Langfuse export + ADK instrumentation once. Returns True if tracing is on."""
    global _initialised
    if _initialised:
        return True
    if not (config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY):
        log.warning("Langfuse keys not set — running without tracing.")
        return False

    from langfuse import Langfuse
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    client = Langfuse(
        public_key=config.LANGFUSE_PUBLIC_KEY,
        secret_key=config.LANGFUSE_SECRET_KEY,
        base_url=config.LANGFUSE_HOST,
        should_export_span=_should_export,
    )
    try:
        if not client.auth_check():
            log.warning("Langfuse auth check failed at %s — traces may be dropped.", config.LANGFUSE_HOST)
    except Exception as e:  # unreachable host, egress block, etc. — don't kill the agent
        log.warning("Langfuse unreachable at %s (%s) — traces will be dropped.", config.LANGFUSE_HOST, e)

    GoogleADKInstrumentor().instrument()
    logging.getLogger("opentelemetry.context").addFilter(_DropTransferDetachNoise())
    _initialised = True
    return True


def flush_tracing() -> None:
    """Send buffered spans before the process exits."""
    if _initialised:
        from langfuse import get_client
        get_client().flush()
