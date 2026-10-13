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


async def test_read_only_tools_exposed():
    assert (await call("__list__"))["tools"] == [
        "get_balance", "get_my_accounts", "get_payees", "list_transactions"]


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
