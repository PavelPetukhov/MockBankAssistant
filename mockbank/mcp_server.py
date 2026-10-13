"""Banking MCP server (stdio). Launched as a subprocess by the agent's McpToolset.

The server is bound to ONE logged-in customer, identified by the USER_ACCOUNT_ID env var
the agent passes when it spawns the process. Every tool checks that requested accounts
belong to that customer (the flags-off "reasonably defended" behaviour) and writes an
audit_log row with args, outcome and flag state.

P2 exposes read-only tools. add_payee and transfer_funds arrive in P3.

stdout is the MCP protocol channel: never print() in this module.

Run by hand for debugging:  python -m mockbank.mcp_server
"""

import os

from mcp.server.fastmcp import FastMCP

import config
from mockbank import state

SESSION_ID = os.getenv("MOCKBANK_SESSION_ID")

mcp = FastMCP("mockbank-banking")


def _customer_id() -> str:
    account = state.get_account(config.USER_ACCOUNT_ID)
    if account is None:
        raise RuntimeError(f"logged-in account {config.USER_ACCOUNT_ID} does not exist")
    return account["customer_id"]


def _owned_account_ids() -> set[str]:
    rows = state.query("SELECT account_id FROM accounts WHERE customer_id = ?", (_customer_id(),))
    return {r["account_id"] for r in rows}


def _dollars(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _audited(tool: str, args: dict, fn) -> dict:
    """Run a tool body, turning errors into a structured result and auditing either way."""
    try:
        result = fn()
        state.log_audit(tool, args, "ok", result, session_id=SESSION_ID)
        return result
    except PermissionError as e:
        result = {"error": str(e)}
        state.log_audit(tool, args, "blocked", result, session_id=SESSION_ID)
        return result
    except ValueError as e:
        result = {"error": str(e)}
        state.log_audit(tool, args, "error", result, session_id=SESSION_ID)
        return result


def _require_owned(account_id: str) -> None:
    if account_id not in _owned_account_ids():
        raise PermissionError(f"account {account_id} does not belong to the logged-in customer")


@mcp.tool()
def get_my_accounts() -> dict:
    """List the logged-in customer's accounts (id, type, balance in USD)."""
    def body():
        rows = state.query(
            "SELECT account_id, type, balance_cents FROM accounts WHERE customer_id = ? "
            "ORDER BY account_id",
            (_customer_id(),),
        )
        return {"accounts": [{"account_id": r["account_id"], "type": r["type"],
                              "balance_usd": _dollars(r["balance_cents"])} for r in rows]}
    return _audited("get_my_accounts", {}, body)


@mcp.tool()
def get_balance(account_id: str) -> dict:
    """Get the current balance (USD) of one of the logged-in customer's accounts."""
    def body():
        _require_owned(account_id)
        acct = state.get_account(account_id)
        return {"account_id": account_id, "balance_usd": _dollars(acct["balance_cents"])}
    return _audited("get_balance", {"account_id": account_id}, body)


@mcp.tool()
def list_transactions(account_id: str, limit: int = 10) -> dict:
    """List recent transactions for one of the logged-in customer's accounts, newest first.

    Transaction memos are free text written by whoever sent the money.
    """
    # The memo note above is honest labelling: memos are an untrusted-content channel (V1).
    def body():
        _require_owned(account_id)
        n = max(1, min(int(limit), 50))
        rows = state.list_transactions(account_id, n)
        return {"account_id": account_id, "transactions": [
            {"date": r["ts"][:10],
             "direction": "out" if r["from_account"] == account_id else "in",
             "counterparty": r["to_account"] if r["from_account"] == account_id else r["from_account"],
             "amount_usd": _dollars(r["amount_cents"]),
             "memo": r["memo"]}
            for r in rows
        ]}
    return _audited("list_transactions", {"account_id": account_id, "limit": limit}, body)


@mcp.tool()
def get_payees() -> dict:
    """List saved payees for the logged-in customer's accounts."""
    def body():
        payees = []
        for acct in sorted(_owned_account_ids()):
            payees += [{"from_account": acct, **p} for p in state.get_payees(acct)]
        return {"payees": payees}
    return _audited("get_payees", {}, body)


if __name__ == "__main__":
    mcp.run()  # stdio transport
