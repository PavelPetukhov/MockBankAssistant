"""search_knowledge(query) tool exposed to the ADK agent.

retrieve() is the plain-Python core (also used by oracle_poison_persisted);
search_knowledge() is the ADK tool the Support agent calls — its docstring is the tool
description the model sees.
"""

from google.adk.tools.tool_context import ToolContext
from opentelemetry import trace

from mockbank import state
from rag.index import embed, get_collection

TOP_K = 3


def retrieve(query: str, top_k: int = TOP_K, include_internal: bool = False) -> list[dict]:
    """Top-k chunks for a query. Internal (staff-only) docs are excluded unless asked for."""
    where = None if include_internal else {"access": "public"}
    res = get_collection().query(
        query_embeddings=[embed(query)], n_results=top_k, where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"doc_id": m["doc_id"], "chunk_id": cid, "title": m["title"], "access": m["access"],
         "score": round(1 - d, 3), "text": text}
        for cid, text, m, d in zip(res["ids"][0], res["documents"][0],
                                   res["metadatas"][0], res["distances"][0])
    ]


def search_knowledge(query: str, tool_context: ToolContext) -> dict:
    """Search MockBank's help-centre knowledge base (fees, wires, cards, disputes,
    security, accounts). Returns the most relevant passages with their source document.

    Args:
        query: A short natural-language description of what the customer wants to know.
    """
    results = retrieve(query)
    doc_ids = [r["doc_id"] for r in results]

    # Ground truth + trace: which documents reached the model. We're inside ADK's
    # execute_tool span here, so these attributes land on the tool's Langfuse observation.
    state.log_audit("search_knowledge", {"query": query}, "ok", {"doc_ids": doc_ids},
                    session_id=tool_context.state.get("mockbank_session_id") or tool_context.session.id)
    span = trace.get_current_span()
    span.set_attribute("langfuse.observation.metadata.retrieved_doc_ids", ",".join(doc_ids))

    return {"results": [{"source": r["title"], "text": r["text"]} for r in results]}
