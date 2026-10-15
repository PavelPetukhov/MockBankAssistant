"""Customer-approval gate for side-effect tools (transfer_funds, add_payee).

Registered as the root agent's before_tool_callback. It uses ADK's native tool
confirmation, so the gate is enforced by the framework, not by the model:

  1st pass: no confirmation yet  -> request one; the tool does NOT run, the turn pauses
            and the client sees an `adk_request_confirmation` call with a hint.
  client:   answers {"confirmed": true|false} (run.py asks y/N; MockBankSession.confirm()).
  2nd pass: ADK re-runs the same call with tool_context.tool_confirmation set
            -> approved: return None so the tool runs; rejected: return an error.

An instruction hidden in a document or memo can make the model *call* transfer_funds,
but the money can't move until the customer approves this specific call.

P4: VULN_EXCESSIVE_AGENCY bypasses this gate. P5: DEFENSE_TRANSFER_CONFIRM adds extra checks.
"""

from google.adk.tools.tool_context import ToolContext

from mockbank import state

CONFIRM_TOOLS = {"transfer_funds", "add_payee"}


def _describe(tool_name: str, args: dict) -> str:
    if tool_name == "transfer_funds":
        memo = f' with memo "{args.get("memo")}"' if args.get("memo") else ""
        return (f"Transfer ${float(args.get('amount_usd', 0)):,.2f} from {args.get('from_account')} "
                f"to {args.get('to_account')}{memo}?")
    if tool_name == "add_payee":
        return f"Add payee {args.get('name')!r} ({args.get('account_id')})?"
    return f"Run {tool_name}({args})?"


def _mark(tool_context: ToolContext, branch: str) -> None:
    # Read by agent.annotate_tool_span to tag this call's trace span.
    tool_context.state[f"temp:guarded_branch:{tool_context.function_call_id}"] = branch


def enforce_tool_policy(tool, args: dict, tool_context: ToolContext) -> dict | None:
    """before_tool_callback: return None to let the tool run, or a dict to replace its result."""
    if tool.name not in CONFIRM_TOOLS:
        return None

    session_id = tool_context.state.get("mockbank_session_id") or tool_context.session.id
    confirmation = tool_context.tool_confirmation

    if confirmation is None:
        tool_context.request_confirmation(hint=_describe(tool.name, args))
        # The pause isn't a result for the model to summarise; without this the model is
        # re-invoked and may simply call the tool again.
        tool_context.actions.skip_summarization = True
        _mark(tool_context, "confirmation_requested")
        state.log_audit(tool.name, args, "pending", {"status": "awaiting customer approval"},
                        session_id=session_id)
        return {"status": "awaiting customer approval"}

    if not confirmation.confirmed:
        _mark(tool_context, "customer_rejected")
        result = {"error": "The customer rejected this action. Do not retry it unless they ask again."}
        state.log_audit(tool.name, args, "blocked", result, session_id=session_id)
        return result

    _mark(tool_context, "customer_approved")
    return None
