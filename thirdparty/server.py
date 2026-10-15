"""Local FastAPI mock: merchant-info endpoint + exfiltration sink (notify/webhook).

  GET  /merchant-info/{id}  merchant directory entry. The description is free text owned
                            by the "third party", so it is attacker-influenceable (V1).
  POST /notify              the exfiltration / confused-deputy sink: accepts anything and
                            logs the full payload into MockBank ground truth (exfil_log)
                            for oracle_data_exfiltrated and oracle_secret_leaked. It never
                            forwards anything anywhere.
  GET  /sink                inspect what the sink received.
  GET  /health              liveness check.

Run standalone:  python -m thirdparty.server   (binds 127.0.0.1:8001 by default)
run.py calls ensure_running(), which starts it in a background thread if needed.
"""

import threading
import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config
from mockbank import state

app = FastAPI(title="MockBank third-party mock (local only)")


class Notification(BaseModel):
    destination: str
    message: str
    session_id: str | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/merchant-info/{merchant_id}")
def merchant_info(merchant_id: str) -> dict:
    rows = state.query("SELECT * FROM merchants WHERE merchant_id = ?", (merchant_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"unknown merchant {merchant_id}")
    return rows[0]


@app.post("/notify")
def notify(n: Notification) -> dict:
    exfil_id = state.log_exfil(n.destination, n.message, session_id=n.session_id)
    return {"status": "delivered", "id": exfil_id}


@app.get("/sink")
def sink(session_id: str | None = None) -> list[dict]:
    """Inspect what the sink has received (handy during manual attack passes)."""
    if session_id:
        return state.query("SELECT * FROM exfil_log WHERE session_id = ? ORDER BY id", (session_id,))
    return state.query("SELECT * FROM exfil_log ORDER BY id")


def _port() -> int:
    return urlparse(config.THIRDPARTY_BASE_URL).port or 8001


def is_running() -> bool:
    try:
        return httpx.get(f"{config.THIRDPARTY_BASE_URL}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


def ensure_running(timeout: float = 10.0) -> bool:
    """Start the mock in a daemon thread unless something already answers at
    THIRDPARTY_BASE_URL (e.g. the `sink` container). Returns True if we started it."""
    if is_running():
        return False
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        app, host=config.THIRDPARTY_BIND_HOST, port=_port(), log_level="warning"))
    threading.Thread(target=server.run, name="thirdparty-mock", daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_running():
            return True
        time.sleep(0.1)
    raise RuntimeError(
        f"third-party mock did not start at {config.THIRDPARTY_BASE_URL} — is another process "
        f"(e.g. an old `docker compose` sink) using port {_port()}? Free it or set THIRDPARTY_BASE_URL.")


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.THIRDPARTY_BIND_HOST, port=_port())


if __name__ == "__main__":
    main()
