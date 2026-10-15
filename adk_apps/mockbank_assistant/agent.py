"""ADK dev UI entry point.

    poetry run adk web adk_apps        # then open http://127.0.0.1:8000

`adk web` treats every subfolder of the directory it's given as an app, so the app lives
in its own folder instead of the repo root (which would list mockbank/, rag/, tests/ ...).

Differences from run.py:
  - One MCP server subprocess is shared by every UI session, so banking audit_log rows
    are tagged session "adk-web"; search_knowledge rows use the UI's own session id.
  - Replies aren't written to the responses table, so oracle_secret_leaked only sees
    outbound payloads here. Use run.py / MockBankSession for oracle-checked trials.
"""

import warnings

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

import egress  # noqa: E402

egress.install()  # fail closed before any network client exists

from agent.agent import build_agent  # noqa: E402
from mockbank.state import reset_state  # noqa: E402
from rag.index import build_index  # noqa: E402
from thirdparty.server import ensure_running  # noqa: E402
from tracing.langfuse_setup import setup_tracing  # noqa: E402

# Each time the UI (re)loads the app it starts a fresh trial, like `python run.py`.
reset_state()
build_index()
ensure_running()  # merchant info + notify sink, unless already up (e.g. docker compose)
setup_tracing()

root_agent = build_agent(session_id="adk-web")
