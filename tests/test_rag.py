"""RAG: index builds offline, retrieval finds the right policy, internal docs stay hidden."""

import pytest

import egress
from rag.index import build_index, load_corpus
from rag.retriever import retrieve


@pytest.fixture(autouse=True)
def index():
    egress.install()  # proves Chroma never phones home (model download, telemetry)
    try:
        build_index()
        yield
    finally:
        egress.uninstall()


@pytest.mark.parametrize("query, doc_id", [
    ("what time is the wire cutoff?", "policy_wire_transfers"),
    ("I lost my debit card", "card_lost_stolen"),
    ("how much is the overdraft fee", "fees_schedule"),
    ("how do I dispute a charge", "disputes_chargebacks"),
])
def test_retrieves_relevant_doc(query, doc_id):
    assert retrieve(query)[0]["doc_id"] == doc_id


def test_internal_docs_hidden_by_default():
    internal = {d["doc_id"] for d in load_corpus() if d["access"] == "internal"}
    assert internal  # the corpus must contain some, or this test proves nothing
    for q in ["fee waiver approval escalation", "fraud review rules mule watchlist"]:
        assert not internal & {r["doc_id"] for r in retrieve(q)}
        assert internal & {r["doc_id"] for r in retrieve(q, include_internal=True)}


def test_injection_fixture_not_in_default_corpus():
    assert all("NOTE TO ASSISTANT" not in d["body"] for d in load_corpus())
