"""Where precedents come from: the corpus, matched lexically or by meaning.

Two backends behind one call, chosen by environment:

``lexical``  token overlap weighted by how discriminative each word is across
             the corpus. No key, no network, no index to build — and no idea
             that "share of the total" and "composition" are the same thing.
``qdrant``   embeddings in a Qdrant vector store. Understands
             the synonyms, and needs a key, a running Qdrant and an index.

Configured with the common ``QDRANT_*`` variables, so one environment can
serve several services::

    NEXCRAFTVIZ_RETRIEVAL   lexical | qdrant   (default: lexical)
    QDRANT_URL / QDRANT_HOST / QDRANT_PORT / QDRANT_API_KEY
    NEXCRAFTVIZ_EMBED_MODEL → OPENAI_EMBED_MODEL → text-embedding-3-small
    NEXCRAFTVIZ_CORPUS_COLLECTION                 (default: nexcraftviz_corpus)

**The shape gate is deterministic in both.** A vector store will cheerfully
return a trend chart for data with no date in it, because the words matched; the
data still cannot support it. So similarity ranks the candidates that the shape
already permits, and never promotes one the data rules out. Vectors improve the
reading of the question, which is the part where meaning matters — not the
reading of the columns, where it does not.

Falling back is explicit and reported. A misconfigured vector store silently
degrading to token matching would look like a working system giving worse
answers for no visible reason.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

DEFAULT_COLLECTION = "nexcraftviz_corpus"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"

#: Backends, in the order `describe()` reports them.
BACKENDS = ("lexical", "qdrant")


@dataclass
class RetrievalConfig:
    """What the environment says about retrieval, and whether it can work."""

    backend: str = "lexical"
    collection: str = DEFAULT_COLLECTION
    embed_model: str = DEFAULT_EMBED_MODEL
    qdrant_url: str = ""
    qdrant_host: str = ""
    qdrant_port: int = 6333
    qdrant_api_key: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def wants_vectors(self) -> bool:
        return self.backend == "qdrant"

    @property
    def is_configured(self) -> bool:
        return bool(self.qdrant_url or self.qdrant_host)

    def client_kwargs(self) -> dict[str, Any]:
        """The usual QdrantSettings fields, so one environment serves several services."""
        kwargs: dict[str, Any] = {}
        if self.qdrant_url:
            kwargs["url"] = self.qdrant_url
        else:
            kwargs["host"] = self.qdrant_host
            kwargs["port"] = self.qdrant_port
        if self.qdrant_api_key:
            kwargs["api_key"] = self.qdrant_api_key
        return kwargs

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "collection": self.collection,
            "embed_model": self.embed_model if self.wants_vectors else "",
            "configured": self.is_configured,
            "reasons": list(self.reasons),
        }


def load_config() -> RetrievalConfig:
    """Read the environment. Never raises — an unusable config is a report."""
    backend = (os.getenv("NEXCRAFTVIZ_RETRIEVAL", "") or "").strip().lower()
    if not backend:
        # The common RETRIEVAL_BACKEND switch, so a stack already using qdrant
        # for retrieval turns this on without a second variable.
        backend = (os.getenv("RETRIEVAL_BACKEND", "") or "").strip().lower()
    config = RetrievalConfig(backend=backend or "lexical")

    if config.backend not in BACKENDS:
        config.reasons.append(
            f"unknown backend {config.backend!r}; using lexical "
            f"(known: {', '.join(BACKENDS)})"
        )
        config.backend = "lexical"

    config.collection = (
        os.getenv("NEXCRAFTVIZ_CORPUS_COLLECTION", "").strip() or DEFAULT_COLLECTION
    )
    config.embed_model = (
        os.getenv("NEXCRAFTVIZ_EMBED_MODEL", "").strip()
        or os.getenv("OPENAI_EMBED_MODEL", "").strip()
        or DEFAULT_EMBED_MODEL
    )
    config.qdrant_url = os.getenv("QDRANT_URL", "").strip()
    config.qdrant_host = os.getenv("QDRANT_HOST", "").strip()
    config.qdrant_api_key = os.getenv("QDRANT_API_KEY", "").strip()
    try:
        config.qdrant_port = int(os.getenv("QDRANT_PORT", "6333") or 6333)
    except ValueError:
        config.reasons.append("QDRANT_PORT is not a number; using 6333")

    if config.wants_vectors and not config.is_configured:
        config.reasons.append("QDRANT_URL or QDRANT_HOST is not set")
    return config


def describe() -> dict[str, Any]:
    """What retrieval will actually do, for `harness check` and /v1/health."""
    config = load_config()
    payload = config.to_dict()
    payload["available"] = True
    payload["effective"] = "lexical"

    if not config.wants_vectors:
        return payload

    try:
        import qdrant_client  # noqa: F401
    except ImportError:
        payload["available"] = False
        payload["reasons"].append(
            "qdrant-client is not installed: pip install 'nexcraftviz[retrieval]'"
        )
        return payload
    if not config.is_configured:
        payload["available"] = False
        return payload

    payload["effective"] = "qdrant"
    return payload


# ---------------------------------------------------------------------------
# the vector backend
# ---------------------------------------------------------------------------

class RetrievalError(RuntimeError):
    """Retrieval was asked for and could not be done."""


def corpus_pairs() -> tuple[Any, ...]:
    """The corpus, loaded once. Shared with the lexical matcher so both
    backends rank over exactly the same body of worked decisions."""
    from nexcraftviz.recommend.precedent import _corpus

    return _corpus()


def document_for(pair: Any) -> str:
    """The text that represents one corpus pair to an embedding model.

    Everything the pair says about *when to use it* — purpose, goal, the
    conditions, the questions it answers — and nothing about the data it was
    drawn from. The point is to match a new question against the decision, not
    against somebody else's numbers.
    """
    parts = [
        pair.chart_type,
        pair.purpose,
        pair.goal,
        *pair.use_when,
        *pair.example_questions,
    ]
    return "\n".join(part for part in parts if part)


def query_for(question: str, signature: str) -> str:
    """The text that represents a request.

    The shape goes in alongside the question because "revenue by region" and
    "revenue over time" differ by their columns as much as their words — but
    the shape *gate* stays deterministic; this only helps the ranking.
    """
    shape = signature.replace("+", ", ") if signature else ""
    return f"{question}\n\ncolumns: {shape}" if shape else question


async def embed(texts: list[str], *, model: str = "") -> list[list[float]]:
    """Embed with the configured provider.

    Imported inside the function, like every other provider call here: the core
    must stay importable with no SDK installed and no key set, and a test
    asserts it.
    """
    if not texts:
        return []
    config = load_config()
    resolved = model or config.embed_model

    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RetrievalError(
            "embeddings need the retrieval extra: pip install 'nexcraftviz[retrieval]'"
        ) from exc

    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise RetrievalError(
            "embeddings need OPENAI_API_KEY. Retrieval falls back to lexical "
            "matching without it — which works, and understands fewer synonyms."
        )

    client = AsyncOpenAI()
    response = await client.embeddings.create(model=resolved, input=texts)
    return [item.embedding for item in response.data]


def client(config: RetrievalConfig | None = None) -> Any:
    """A Qdrant client, or a readable error saying which part is missing."""
    config = config or load_config()
    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:
        raise RetrievalError(
            "qdrant-client is not installed: pip install 'nexcraftviz[retrieval]'"
        ) from exc
    if not config.is_configured:
        raise RetrievalError("Qdrant is not configured (set QDRANT_URL or QDRANT_HOST)")
    return QdrantClient(**config.client_kwargs())


async def index_corpus(*, recreate: bool = False, qdrant: Any = None) -> dict[str, Any]:
    """Embed every corpus pair and upsert it.

    Run once per corpus version. The corpus is a shipped data file, so this is
    a build step rather than something that happens on a request path.
    """
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from nexcraftviz.corpus.loader import seed

    config = load_config()
    store = qdrant or client(config)
    pairs = list(seed().pairs)
    vectors = await embed([document_for(p) for p in pairs], model=config.embed_model)
    if not vectors:
        raise RetrievalError("no corpus pairs to index")

    if recreate:
        store.recreate_collection(
            collection_name=config.collection,
            vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
        )

    store.upsert(
        collection_name=config.collection,
        points=[
            PointStruct(
                id=index,
                vector=vector,
                payload={"name": pair.name, "chart_type": pair.chart_type},
            )
            for index, (pair, vector) in enumerate(zip(pairs, vectors, strict=False))
        ],
    )
    return {
        "collection": config.collection,
        "indexed": len(pairs),
        "dimensions": len(vectors[0]),
        "model": config.embed_model,
    }


async def similar(
    question: str, signature: str, *, limit: int = 40, qdrant: Any = None
) -> dict[str, float]:
    """``{pair name: similarity}`` for the most semantically similar pairs.

    Returns names rather than decisions: the caller still applies the shape gate
    and still ranks by chart type, so a vector store cannot promote a chart the
    data does not support.
    """
    config = load_config()
    store = qdrant or client(config)
    vectors = await embed([query_for(question, signature)], model=config.embed_model)
    if not vectors:
        return {}

    hits = store.search(
        collection_name=config.collection, query_vector=vectors[0], limit=limit
    )
    return {
        str((hit.payload or {}).get("name") or ""): float(hit.score)
        for hit in hits
        if (hit.payload or {}).get("name")
    }
