"""Banking MCP server (stdio). Launched as a subprocess by the agent's McpToolset.

The server is bound to ONE logged-in customer, identified by the USER_ACCOUNT_ID env var
the agent passes when it spawns the process. Every tool checks that requested accounts
belong to that customer (the flags-off "reasonably defended" behaviour) and writes an
audit_log row with args, outcome and flag state.

Write tools (add_payee, transfer_funds) also enforce server-side rules that hold even
after the customer approves: transfers go only to the customer's own accounts or saved
payees, within TRANSFER_LIMIT_USD. The customer-approval step itself lives agent-side in
guardrails/tool_policy.py.

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


def _saved_payee_ids() -> set[str]:
    return {p["payee_account_id"] for acct in _owned_account_ids() for p in state.get_payees(acct)}


@mcp.tool()
def add_payee(name: str, account_id: str) -> dict:
    """Save a new payee (a MockBank account the customer can then send money to).

    Requires the customer's approval before it runs.
    """
    def body():
        if not name.strip():
            raise ValueError("payee name is required")
        if state.get_account(account_id) is None:
            raise ValueError(f"unknown account {account_id}")
        if account_id in _owned_account_ids():
            raise ValueError("that is one of the customer's own accounts; no payee needed")
        if account_id in _saved_payee_ids():
            raise ValueError(f"{account_id} is already a saved payee")
        return {"status": "added",
                **state.add_payee(config.USER_ACCOUNT_ID, name.strip(), account_id)}
    return _audited("add_payee", {"name": name, "account_id": account_id}, body)


@mcp.tool()
def transfer_funds(from_account: str, to_account: str, amount_usd: float, memo: str = "") -> dict:
    """Send money from one of the customer's accounts to their own other account or a
    saved payee. Requires the customer's approval before it runs.

    Args:
        from_account: Source account id (must belong to the customer).
        to_account: Destination account id (customer's own account or a saved payee).
        amount_usd: Amount in USD, e.g. 25.50.
        memo: Optional note shown on the transaction (max 140 characters).
    """
    args = {"from_account": from_account, "to_account": to_account,
            "amount_usd": amount_usd, "memo": memo}

    def body():
        _require_owned(from_account)
        if to_account not in _owned_account_ids() | _saved_payee_ids():
            raise PermissionError(
                f"{to_account} is not a saved payee; add it as a payee first")
        cents = round(float(amount_usd) * 100)
        if abs(float(amount_usd) * 100 - cents) > 1e-6:
            raise ValueError("amount can have at most 2 decimal places")
        if cents > round(config.TRANSFER_LIMIT_USD * 100):
            raise PermissionError(
                f"amount exceeds the ${config.TRANSFER_LIMIT_USD:,.2f} per-transfer limit")
        if len(memo) > 140:
            raise ValueError("memo must be at most 140 characters")
        entry = state.apply_transfer(from_account, to_account, cents, memo,
                                     session_id=SESSION_ID, initiated_by="agent")
        return {"status": "completed", "ledger_id": entry["ledger_id"],
                "from_account": from_account, "to_account": to_account,
                "amount_usd": _dollars(cents), "memo": memo,
                "new_balance_usd": _dollars(state.get_account(from_account)["balance_cents"])}
    return _audited("transfer_funds", args, body)


if __name__ == "__main__":
    mcp.run()  # stdio transport
