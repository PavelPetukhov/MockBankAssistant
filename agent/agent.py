"""ADK agents: the MockBank front-desk assistant (root) and its Support sub-agent.

    mockbank_assistant (root LlmAgent)
    ├─ banking tools from the local MCP server (get_my_accounts, get_balance, ...)
    └─ sub_agents:
       └─ support_agent (LlmAgent)
          └─ search_knowledge (RAG over rag/corpus)

ADK gives each agent an automatic transfer_to_agent tool, so the root can hand policy
questions to support_agent and support_agent can hand account questions back.

P3 adds transfer_funds / add_payee and the third-party tools.
"""

import os
import sys

from google.adk.agents import LlmAgent
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment
from opentelemetry import trace

import config
from agent.prompts import get_root_instruction, get_support_instruction
from mockbank import state
from rag.retriever import search_knowledge

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_model():
    """Gemini by default (ADK-native string); any LiteLLM-supported model otherwise."""
    if config.MODEL_PROVIDER == "gemini":
        return config.MODEL_NAME
    # Lazy import: LiteLLM needs the optional `google-adk[extensions]` extra.
    from google.adk.models.lite_llm import LiteLlm
    return LiteLlm(model=config.MODEL_NAME)


def build_banking_toolset(session_id: str) -> McpToolset:
    """Spawn the banking MCP server as a stdio subprocess bound to this session's customer."""
    env = {
        **get_default_environment(),  # minimal PATH/HOME etc.; don't leak API keys to the server
        "USER_ACCOUNT_ID": config.USER_ACCOUNT_ID,
        "MOCKBANK_SESSION_ID": session_id,
        "MOCKBANK_DB_PATH": config.MOCKBANK_DB_PATH,
        # Pass this process's flag view explicitly so both sides always agree.
        **{k: str(v).lower() for k, v in config.flag_snapshot().items()},
    }
    return McpToolset(
        # VERIFY (google-adk 2.7 / mcp 1.30): StdioConnectionParams wraps StdioServerParameters.
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable, args=["-m", "mockbank.mcp_server"], cwd=REPO_ROOT, env=env,
            ),
            timeout=15,
        ),
    )


def annotate_tool_span(tool, args, tool_context, tool_response):
    """after_tool_callback: tag the tool's trace span with flag state and guard outcome.

    ADK runs this inside the execute_tool span, so attributes land on that tool's
    Langfuse observation. Returning None keeps the tool response unchanged.
    """
    span = trace.get_current_span()
    active = [k for k, v in config.flag_snapshot().items() if v]
    span.set_attribute("langfuse.observation.metadata.active_flags", ",".join(active) or "none")
    # No guarded branches exist yet; P4/P5 set this when a vuln or defense path is taken.
    span.set_attribute("langfuse.observation.metadata.guarded_branch", "none")
    return None


def build_agent(session_id: str) -> LlmAgent:
    """Build the agent tree. Runner.close() closes the MCP toolset (and its subprocess)."""
    account = state.get_account(config.USER_ACCOUNT_ID)
    customer = state.query("SELECT * FROM customers WHERE customer_id = ?", (account["customer_id"],))[0]
    model = build_model()

    support = LlmAgent(
        name="support_agent",
        description="Answers questions about MockBank policies, fees, cards, wires, disputes "
                    "and security using the knowledge base.",
        model=model,
        instruction=get_support_instruction(),
        tools=[search_knowledge],
        after_tool_callback=annotate_tool_span,
    )
    root = LlmAgent(
        name="mockbank_assistant",
        description="MockBank front desk: account questions, hands policy questions to support.",
        model=model,
        instruction=get_root_instruction(customer["name"], customer["customer_id"], config.USER_ACCOUNT_ID),
        tools=[build_banking_toolset(session_id)],
        sub_agents=[support],
        after_tool_callback=annotate_tool_span,
    )
    return root
