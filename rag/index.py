"""Build and persist the vector index over the corpus directory.

Corpus files are plain text with a small header:

    title: Wire Transfer Policy
    access: public          # or: internal (staff-only, never shown to customers by default)
    ---
    Paragraph one...

    Paragraph two...

Each paragraph becomes one chunk in a persistent Chroma collection under var/chroma.

Embeddings are a local hashed bag-of-words (see embed()). That's deliberately simple:
Chroma's default embedding model is downloaded from the internet on first use, which the
egress allowlist forbids, and lexical matching is plenty for a ~10-document corpus.
"""

import hashlib
import os
import re

import chromadb
from chromadb.config import Settings

import config

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "corpus")
COLLECTION = "mockbank_knowledge"

_DIM = 1024
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its my "
    "of on or our the this to was what when which will with you your".split()
)


def _tokens(text: str) -> list[str]:
    # Crude plural folding ("fees" -> "fee") so queries match more often.
    return [t[:-1] if len(t) > 3 and t.endswith("s") else t
            for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def embed(text: str) -> list[float]:
    """Hash unigrams and bigrams into a fixed-size, L2-normalised vector."""
    vec = [0.0] * _DIM
    toks = _tokens(text)
    for feature in toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]:
        # md5, not hash(): Python's hash() is salted per process, embeddings must be stable.
        bucket = int.from_bytes(hashlib.md5(feature.encode()).digest()[:4], "little") % _DIM
        vec[bucket] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def parse_doc(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        header, _, body = f.read().partition("\n---\n")
    meta = dict(line.split(":", 1) for line in header.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    return {
        "doc_id": os.path.splitext(os.path.basename(path))[0],
        "title": meta["title"],
        "access": meta.get("access", "public"),
        "body": body.strip(),
    }


def load_corpus() -> list[dict]:
    return [parse_doc(os.path.join(CORPUS_DIR, name))
            for name in sorted(os.listdir(CORPUS_DIR)) if name.endswith(".txt")]


def chroma_path() -> str:
    """The index lives next to the bank DB (var/chroma), so one volume holds all state."""
    return os.path.join(os.path.dirname(config.MOCKBANK_DB_PATH), "chroma")


def _client():
    return chromadb.PersistentClient(path=chroma_path(), settings=Settings(anonymized_telemetry=False))


def get_collection():
    client = _client()
    # embedding_function=None: we always pass our own vectors, so Chroma never loads a model.
    return client.get_or_create_collection(
        COLLECTION, embedding_function=None, metadata={"hnsw:space": "cosine"}
    )


def build_index() -> int:
    """Drop and rebuild the collection from the corpus. Returns the number of chunks."""
    client = _client()
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    collection = get_collection()

    ids, texts, metas = [], [], []
    for doc in load_corpus():
        for i, para in enumerate(p.strip() for p in doc["body"].split("\n\n") if p.strip()):
            ids.append(f"{doc['doc_id']}#{i}")
            texts.append(para)
            metas.append({"doc_id": doc["doc_id"], "title": doc["title"], "access": doc["access"]})
    collection.add(ids=ids, documents=texts, metadatas=metas,
                   embeddings=[embed(f"{m['title']}\n{t}") for m, t in zip(metas, texts)])
    return len(ids)


if __name__ == "__main__":
    print(f"Indexed {build_index()} chunks into {chroma_path()}")
