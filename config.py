"""Central configuration: feature flags, model settings, egress allowlist."""

import os


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


# --- Vulnerability toggles (all off by default) ---
VULN_INDIRECT_INJECTION = _bool("VULN_INDIRECT_INJECTION")
VULN_EXCESSIVE_AGENCY = _bool("VULN_EXCESSIVE_AGENCY")
VULN_EXFIL = _bool("VULN_EXFIL")
VULN_RAG_POISONING = _bool("VULN_RAG_POISONING")
VULN_SECRET_IN_PROMPT = _bool("VULN_SECRET_IN_PROMPT")
VULN_MCP_TOOL_POISONING = _bool("VULN_MCP_TOOL_POISONING")

# --- Defense toggles ---
DEFENSE_INPUT_FILTER = _bool("DEFENSE_INPUT_FILTER")
DEFENSE_OUTPUT_FILTER = _bool("DEFENSE_OUTPUT_FILTER")
DEFENSE_TRANSFER_CONFIRM = _bool("DEFENSE_TRANSFER_CONFIRM")

# --- Model ---
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "gemini")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# --- Langfuse / tracing ---
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

# --- Egress allowlist (fail-closed: reject any outbound host not listed) ---
# Hosts are matched as exact strings against the netloc of outbound URLs.
EGRESS_ALLOWLIST: set[str] = {
    "localhost",
    "127.0.0.1",
    "generativelanguage.googleapis.com",  # Gemini API
    "cloud.langfuse.com",                 # Langfuse (overridden by LANGFUSE_HOST)
}

# Add the configured Langfuse host dynamically
if LANGFUSE_HOST:
    from urllib.parse import urlparse
    _lf_host = urlparse(LANGFUSE_HOST).netloc or LANGFUSE_HOST
    EGRESS_ALLOWLIST.add(_lf_host)

# --- Third-party mock service ---
THIRDPARTY_BASE_URL = os.getenv("THIRDPARTY_BASE_URL", "http://localhost:8001")
