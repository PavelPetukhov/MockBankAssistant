"""One-command local entrypoint: the MockBank Assistant CLI chat loop.

Starts the local third-party mock (merchant info + notify sink) in the background unless
one is already running at THIRDPARTY_BASE_URL, then opens the chat.

    python run.py                          # fresh trial: reset bank state + rebuild RAG index
    python run.py --no-reset               # keep state from earlier runs (persistence tests)
    python run.py --scenario-id exfil-01   # tag the Langfuse traces for this session

Type 'exit' or Ctrl-D to quit.
"""

import argparse
import asyncio
import logging
import warnings

# ADK warns about experimental features at import time; silence before importing it.
warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

import egress  # noqa: E402

egress.install()  # fail closed before any network client exists

import config  # noqa: E402
from agent.session import MockBankSession  # noqa: E402
from mockbank.state import reset_state  # noqa: E402
from rag.index import build_index  # noqa: E402
from thirdparty.server import ensure_running  # noqa: E402
from tracing.langfuse_setup import flush_tracing, setup_tracing  # noqa: E402

DIM, YELLOW, RESET = "\033[2m", "\033[33m", "\033[0m"


async def chat(args: argparse.Namespace) -> None:
    async with MockBankSession(session_id=args.session_id, scenario_id=args.scenario_id,
                               run_id=args.run_id) as session:
        print(f"MockBank Assistant — logged in as {config.USER_ACCOUNT_ID} "
              f"(session {session.session_id}). Type 'exit' to quit.\n")
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.lower() in {"exit", "quit"}:
                break
            if not text:
                continue
            try:
                turn = await session.send(text)
                show(turn)
                # Side-effect tools pause until the customer approves this exact call.
                while turn.pending_confirmations:
                    pending = turn.pending_confirmations[0]
                    answer = input(f"{YELLOW}  approve? {pending['hint']} [y/N] {RESET}").strip().lower()
                    turn = await session.confirm(pending["id"], approved=answer in {"y", "yes"})
                    show(turn)
            except (EOFError, KeyboardInterrupt):
                print()
                break
            except Exception as e:  # model outage, quota, etc. — keep the chat alive
                print(f"bank> [error: {type(e).__name__}: {str(e)[:300]}] — try again.\n")


def show(turn) -> None:
    for call in turn.tool_calls:
        print(f"{DIM}  [{call['agent']}] {call['tool']}({call['args']}){RESET}")
    if turn.text:
        print(f"bank> {turn.text}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="MockBank Assistant (deliberately vulnerable target)")
    parser.add_argument("--no-reset", action="store_true", help="keep bank state and RAG index")
    parser.add_argument("--session-id")
    parser.add_argument("--scenario-id")
    parser.add_argument("--run-id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if config.MODEL_PROVIDER == "gemini" and not config.GEMINI_API_KEY:
        raise SystemExit("GEMINI_API_KEY is not set — add it to .env (see .env.example).")
    if not args.no_reset:
        reset_state()
        build_index()
    if ensure_running():
        print(f"{DIM}third-party mock: started at {config.THIRDPARTY_BASE_URL}{RESET}")
    tracing = setup_tracing()
    print(f"{DIM}tracing: {'on → ' + config.LANGFUSE_HOST if tracing else 'off'}{RESET}")

    try:
        asyncio.run(chat(args))
    finally:
        flush_tracing()


if __name__ == "__main__":
    main()
