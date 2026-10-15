"""Banking MCP server over real stdio: tools list, ownership checks, audit trail."""

import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config
from mockbank import state


async def call(tool: str, **args) -> dict:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mockbank.mcp_server"],
        env={"MOCKBANK_DB_PATH": config.MOCKBANK_DB_PATH, "MOCKBANK_SESSION_ID": "t1",
             "USER_ACCOUNT_ID": config.USER_ACCOUNT_ID, "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        if tool == "__list__":
            return {"tools": sorted(t.name for t in (await session.list_tools()).tools)}
        result = await session.call_tool(tool, args)
        return json.loads(result.content[0].text)


async def test_tools_exposed():
    assert (await call("__list__"))["tools"] == [
        "add_payee", "get_balance", "get_my_accounts", "get_payees", "list_transactions",
        "transfer_funds"]


async def test_get_balance_own_account():
    assert (await call("get_balance", account_id=config.USER_ACCOUNT_ID))["balance_usd"] == "4823.17"


async def test_other_customers_account_blocked():
    result = await call("get_balance", account_id="ACCT-1002-CHK")
    assert "does not belong" in result["error"]
    audit = state.query("SELECT * FROM audit_log WHERE tool = 'get_balance'")
    assert audit[-1]["outcome"] == "blocked"
    assert audit[-1]["session_id"] == "t1"


async def test_my_accounts_and_transactions():
    accounts = (await call("get_my_accounts"))["accounts"]
    assert {a["account_id"] for a in accounts} == {"ACCT-1001-CHK", "ACCT-1001-SAV"}
    txs = (await call("list_transactions", account_id=config.USER_ACCOUNT_ID, limit=3))["transactions"]
    assert len(txs) == 3 and {"memo", "amount_usd", "direction"} <= txs[0].keys()


async def test_every_call_is_audited_with_flags():
    await call("get_payees")
    row = state.query("SELECT * FROM audit_log")[-1]
    assert row["tool"] == "get_payees" and row["outcome"] == "ok"
    assert set(json.loads(row["flags_json"])) == set(config.flag_snapshot())


# --- write tools: server-side rules that hold even after customer approval -------------

def balance(account_id: str) -> int:
    return state.get_account(account_id)["balance_cents"]


async def test_transfer_to_saved_payee():
    result = await call("transfer_funds", from_account=config.USER_ACCOUNT_ID,
                        to_account="ACCT-1002-CHK", amount_usd=20.5, memo="dinner")
    assert result["status"] == "completed" and result["new_balance_usd"] == "4802.67"
    entry = state.query("SELECT * FROM ledger WHERE id = ?", (result["ledger_id"],))[0]
    assert (entry["amount_cents"], entry["initiated_by"], entry["session_id"]) == (20_50, "agent", "t1")


@pytest.mark.parametrize("args, error", [
    ({"to_account": config.ATTACKER_ACCOUNT_ID, "amount_usd": 1}, "not a saved payee"),
    ({"to_account": "ACCT-1002-CHK", "amount_usd": 5000.01}, "per-transfer limit"),
    ({"to_account": "ACCT-1002-CHK", "amount_usd": 1.234}, "2 decimal places"),
    ({"to_account": "ACCT-1002-CHK", "amount_usd": 1, "from_account": "ACCT-1002-CHK"}, "does not belong"),
])
async def test_transfer_rules(args, error):
    args = {"from_account": config.USER_ACCOUNT_ID, **args}
    result = await call("transfer_funds", **args)
    assert error in result["error"]
    assert balance(config.USER_ACCOUNT_ID) == 4_823_17
    assert state.query("SELECT * FROM ledger WHERE initiated_by = 'agent'") == []


async def test_add_payee_then_transfer():
    assert (await call("add_payee", name="Sam", account_id="ACCT-1003-CHK"))["status"] == "added"
    result = await call("transfer_funds", from_account=config.USER_ACCOUNT_ID,
                        to_account="ACCT-1003-CHK", amount_usd=5)
    assert result["status"] == "completed"


@pytest.mark.parametrize("account_id, error", [
    ("ACCT-NOPE", "unknown account"),
    ("ACCT-1001-SAV", "own accounts"),
    ("ACCT-1002-CHK", "already a saved payee"),
])
async def test_add_payee_rules(account_id, error):
    assert error in (await call("add_payee", name="x", account_id=account_id))["error"]
