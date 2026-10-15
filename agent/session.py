"""One chat session with the MockBank agent — the programmatic surface for run.py,
tests, and AgentDrill.

    async with MockBankSession(session_id="run-42", scenario_id="exfil-01") as s:
        reply = await s.send("What's my balance?")

Each send() is one Langfuse trace, tagged with the session, scenario/run ids and the
active flags, and the final reply is recorded in MockBank ground truth (responses table)
for oracle_secret_leaked.

Side-effect tools (transfer_funds, add_payee) pause for customer approval. The paused
calls come back in TurnResult.pending_confirmations; answer each with confirm():

    turn = await s.send("Send $20 to Jordan")
    for p in turn.pending_confirmations:      # {"id", "tool", "args", "hint"}
        turn = await s.confirm(p["id"], approved=True)
"""

import uuid
from dataclasses import dataclass, field

from google.adk.flows.llm_flows.tools._functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

import config
from agent.agent import build_agent
from mockbank import state

APP_NAME = "mockbank"


@dataclass
class TurnResult:
    text: str
    tool_calls: list[dict] = field(default_factory=list)  # {"agent", "tool", "args"}
    agents: list[str] = field(default_factory=list)       # agents that spoke, in order
    pending_confirmations: list[dict] = field(default_factory=list)  # {"id","tool","args","hint"}


class MockBankSession:
    def __init__(self, session_id: str | None = None, scenario_id: str | None = None,
                 run_id: str | None = None):
        self.session_id = session_id or f"sess-{uuid.uuid4().hex[:8]}"
        self.scenario_id = scenario_id
        self.run_id = run_id
        self.user_id = config.USER_ACCOUNT_ID

    async def __aenter__(self) -> "MockBankSession":
        self.agent = build_agent(self.session_id)
        self._sessions = InMemorySessionService()
        self.runner = Runner(app_name=APP_NAME, agent=self.agent, session_service=self._sessions)
        await self._sessions.create_session(
            app_name=APP_NAME, user_id=self.user_id, session_id=self.session_id,
            # Read by tools (e.g. search_knowledge) to tag audit_log rows.
            state={"mockbank_session_id": self.session_id},
        )
        return self

    async def __aexit__(self, *exc) -> None:
        # Closing the runner closes the MCP toolset and stops the server subprocess.
        await self.runner.close()

    def _trace_attributes(self) -> dict:
        active = [k for k, v in config.flag_snapshot().items() if v]
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "trace_name": f"mockbank-turn:{self.scenario_id or 'manual'}",
            "tags": ["mockbank", *(f"flag:{k}" for k in active)],
            # VERIFY (langfuse 4.x): propagated metadata values must be short strings.
            "metadata": {
                "scenario_id": self.scenario_id or "manual",
                "run_id": self.run_id or "",
                "active_flags": ",".join(active)[:200] or "none",
            },
        }

    async def send(self, text: str) -> TurnResult:
        """Send one customer message."""
        return await self._run(types.Content(role="user", parts=[types.Part(text=text)]))

    async def confirm(self, confirmation_id: str, approved: bool) -> TurnResult:
        """Approve or reject a paused side-effect tool call (see pending_confirmations)."""
        response = types.FunctionResponse(
            id=confirmation_id, name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
            response={"confirmed": approved},
        )
        return await self._run(types.Content(role="user", parts=[types.Part(function_response=response)]))

    async def _run(self, message: types.Content) -> TurnResult:
        from langfuse import propagate_attributes

        result = TurnResult(text="")
        final_parts: list[str] = []

        # propagate_attributes is a no-op for export when tracing isn't initialised.
        with propagate_attributes(**self._trace_attributes()):
            async for event in self.runner.run_async(
                user_id=self.user_id, session_id=self.session_id, new_message=message
            ):
                if event.author and event.author != "user" and (
                    not result.agents or result.agents[-1] != event.author
                ):
                    result.agents.append(event.author)
                for call in event.get_function_calls():
                    if call.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
                        # VERIFY (google-adk 2.11): args = {originalFunctionCall, toolConfirmation}.
                        original = call.args["originalFunctionCall"]
                        result.pending_confirmations.append({
                            "id": call.id, "tool": original["name"],
                            "args": dict(original.get("args") or {}),
                            "hint": call.args["toolConfirmation"].get("hint", ""),
                        })
                    else:
                        result.tool_calls.append(
                            {"agent": event.author, "tool": call.name, "args": dict(call.args or {})}
                        )
                if event.is_final_response() and event.content and event.content.parts:
                    final_parts += [p.text for p in event.content.parts if p.text]

        result.text = "\n".join(final_parts).strip()
        if result.text:
            state.log_response(result.text, session_id=self.session_id)
        return result
