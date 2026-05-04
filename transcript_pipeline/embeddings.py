# transcript_pipeline.embeddings
# =============================================================================
# Token-windowed conversation embeddings + per-window stage labels, both
# cached as fp16 NumPy arrays per conversation.
#
# Why this module exists (v0.4):
#   The temporal weaver alone is too coarse. Adam runs 3-5 projects in a
#   single 6h window; pure timestamp filtering pulls in every parallel chat
#   regardless of topic. Embeddings filter for semantic relevance to the
#   anchor — only conversations that actually overlap the anchor's topic
#   make it into the woven transcript.
#
# Design (per Adam's v0.4 spec):
#
#   - Token-based windows (~2500 tokens, 50% overlap by default). Turn
#     counts are too variable — some Claude Code turns are 100 tokens, some
#     are 8K. Token windows give consistent semantic density.
#   - Embedder: qwen3-embedding:8b via Ollama. Small embedders collapse
#     parallel technical projects into near-identical vectors because they
#     share surface vocabulary (llm, evals, fastapi, supabase). 8B has
#     capacity to keep ghostlogic / poaw / margot actually separate.
#   - Aggregation: max cosine across candidate windows. The question is
#     "did *any* part of this chat overlap the anchor?" — mean would wash
#     out partial matches.
#   - Two-stage retrieval: time + conversation-level cosine first (cheap),
#     window-level max cosine on survivors only.
#   - Classifier: qwen3:8b (instruct, separate generative model — embedding
#     models cannot classify). Loads SPEC.md as system prompt; outputs
#     {stage, outcome, confidence} per window.
#   - Storage: out/embeddings/<convo_id>.npz with windows array (fp16),
#     conversation-level vector (fp16), labels array (object dtype with
#     stage/outcome/confidence per window).
#   - Cache key: (convo_id, embedder_tag, window_tokens, window_overlap,
#     classifier_tag). Re-tuning window params or swapping models doesn't
#     silently use stale vectors. Embeddings and labels invalidate
#     independently.
#
# Honest constraints:
#   - tiktoken's o200k_base isn't qwen3's exact tokenizer. Window boundaries
#     drift by a few tokens. For windowing semantics this is fine — the
#     point is consistent density, not byte-equivalence.
#   - One vector per conversation (concat-then-truncate) loses topic drift.
#     Windowed level recovers it; the conversation-level pass exists only
#     as a cheap pre-filter.
# =============================================================================

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable

import numpy as np


# Lazy tiktoken import: tiktoken is fast but non-trivial to install on some
# systems; we only need it when actually embedding.
def _get_encoder():
    import tiktoken

    try:
        return tiktoken.get_encoding("o200k_base")
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


# ---------------------------------------------------------------------------
# Defaults (overridden by CLI flags)
# ---------------------------------------------------------------------------

DEFAULT_EMBEDDER_TAG = "qwen3-embedding:8b"
DEFAULT_CLASSIFIER_TAG = "qwen3:8b"
DEFAULT_WINDOW_TOKENS = 2500
DEFAULT_WINDOW_OVERLAP = 0.5
DEFAULT_SEMANTIC_THRESHOLD = 0.55
OLLAMA_HOST = "http://localhost:11434"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WindowLabel:
    """Per-window classifier output. Aligned 1:1 with windows array."""

    stage: str  # one of the 9 from SPEC.md
    outcome: str  # short phrase, 3-7 words
    confidence: float  # 0.0-1.0
    requires_human: bool  # True if confidence < threshold

    def to_obj(self) -> dict:
        return asdict(self)


@dataclass
class CachedEmbeddings:
    """What lives in out/embeddings/<convo_id>.npz, plus the cache key it
    was computed under."""

    convo_id: str
    embedder_tag: str
    classifier_tag: str | None
    window_tokens: int
    window_overlap: float

    convo_vector: np.ndarray  # shape (D,), fp16
    window_vectors: np.ndarray  # shape (N, D), fp16
    window_texts: list[str]  # raw window text, for re-classification
    labels: list[WindowLabel] = field(default_factory=list)

    def has_labels(self) -> bool:
        return len(self.labels) == len(self.window_texts)


# ---------------------------------------------------------------------------
# Tokenization + windowing
# ---------------------------------------------------------------------------


def stream_text(turns) -> str:
    """Flatten an iterable of ParsedTurn (or any object with .body) into one
    string. Used by both embedding paths (conversation-level concat and
    window slicing)."""
    parts: list[str] = []
    for t in turns:
        body = getattr(t, "body", "") or ""
        if not body.strip():
            continue
        agent = getattr(t, "agent", None)
        if agent is not None:
            parts.append(f"[{getattr(agent, 'value', str(agent))}] {body}")
        else:
            parts.append(body)
    return "\n\n".join(parts)


def windowize(
    text: str,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    overlap: float = DEFAULT_WINDOW_OVERLAP,
) -> list[str]:
    """Slice text into overlapping token windows. Returns list of window
    texts (decoded back from token ids). At least one window always
    returned, even for short text."""
    if window_tokens <= 0:
        raise ValueError("window_tokens must be > 0")
    if not 0.0 <= overlap < 1.0:
        raise ValueError("overlap must be in [0, 1)")
    if not text.strip():
        return [""]

    enc = _get_encoder()
    ids = enc.encode(text, disallowed_special=())
    if not ids:
        return [""]
    if len(ids) <= window_tokens:
        return [enc.decode(ids)]

    stride = max(1, int(window_tokens * (1.0 - overlap)))
    windows: list[str] = []
    i = 0
    while i < len(ids):
        chunk = ids[i : i + window_tokens]
        if not chunk:
            break
        windows.append(enc.decode(chunk))
        if i + window_tokens >= len(ids):
            break
        i += stride
    return windows


# ---------------------------------------------------------------------------
# Ollama embedding client
# ---------------------------------------------------------------------------


def _embed_one(text: str, model: str = DEFAULT_EMBEDDER_TAG, host: str = OLLAMA_HOST) -> np.ndarray:
    """Single embedding call to Ollama. Returns fp32 vector; caller casts
    to fp16 before storage."""
    import urllib.request

    body = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    vec = data.get("embedding") or data.get("embeddings")
    if not vec:
        raise RuntimeError(f"ollama returned no embedding: {data}")
    if isinstance(vec[0], list):
        vec = vec[0]
    return np.asarray(vec, dtype=np.float32)


def embed_text(
    text: str,
    *,
    model: str = DEFAULT_EMBEDDER_TAG,
    host: str = OLLAMA_HOST,
) -> np.ndarray:
    """Embed a single string. Truncated by the embedder to its context
    limit; we don't pre-truncate here — Ollama handles it."""
    return _embed_one(text, model=model, host=host)


def embed_windows(
    windows: list[str],
    *,
    model: str = DEFAULT_EMBEDDER_TAG,
    host: str = OLLAMA_HOST,
) -> np.ndarray:
    """Embed a list of window strings → (N, D) fp32 array."""
    if not windows:
        return np.zeros((0, 1), dtype=np.float32)
    vecs = [_embed_one(w, model=model, host=host) for w in windows]
    return np.vstack(vecs)


# ---------------------------------------------------------------------------
# Cosine + retrieval
# ---------------------------------------------------------------------------


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors (any dtype)."""
    af = a.astype(np.float32)
    bf = b.astype(np.float32)
    na = np.linalg.norm(af)
    nb = np.linalg.norm(bf)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(af, bf) / (na * nb))


def max_cosine(query: np.ndarray, candidates: np.ndarray) -> float:
    """Max cosine of `query` (1-D) against rows of `candidates` (N, D).
    Returns 0.0 if no candidates."""
    if candidates.size == 0:
        return 0.0
    q = query.astype(np.float32)
    c = candidates.astype(np.float32)
    qn = np.linalg.norm(q)
    cn = np.linalg.norm(c, axis=1)
    cn = np.where(cn == 0.0, 1.0, cn)
    if qn == 0.0:
        return 0.0
    sims = (c @ q) / (cn * qn)
    return float(np.max(sims))


# ---------------------------------------------------------------------------
# Storage (NPZ with both arrays + cache key as JSON sidecar)
# ---------------------------------------------------------------------------


def _cache_path(out_dir: Path, convo_id: str) -> Path:
    return Path(out_dir) / "embeddings" / f"{convo_id}.npz"


def _meta_path(out_dir: Path, convo_id: str) -> Path:
    return Path(out_dir) / "embeddings" / f"{convo_id}.meta.json"


def save_cache(out_dir: Path, c: CachedEmbeddings) -> Path:
    """Write fp16 npz + meta sidecar."""
    path = _cache_path(out_dir, c.convo_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    label_objs = [l.to_obj() for l in c.labels]
    np.savez_compressed(
        path,
        convo_vector=c.convo_vector.astype(np.float16),
        window_vectors=c.window_vectors.astype(np.float16),
        window_texts=np.array(c.window_texts, dtype=object),
        labels=np.array(json.dumps(label_objs), dtype=object),
    )

    meta = {
        "convo_id": c.convo_id,
        "embedder_tag": c.embedder_tag,
        "classifier_tag": c.classifier_tag,
        "window_tokens": c.window_tokens,
        "window_overlap": c.window_overlap,
        "n_windows": len(c.window_texts),
        "has_labels": c.has_labels(),
    }
    _meta_path(out_dir, c.convo_id).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def load_cache(
    out_dir: Path,
    convo_id: str,
    *,
    embedder_tag: str,
    window_tokens: int,
    window_overlap: float,
    classifier_tag: str | None = None,
) -> CachedEmbeddings | None:
    """Return cached if (convo_id, embedder, window params) match; else None.
    Classifier mismatch is OK — embeddings still valid; caller re-runs labels."""
    meta_p = _meta_path(out_dir, convo_id)
    npz_p = _cache_path(out_dir, convo_id)
    if not (meta_p.exists() and npz_p.exists()):
        return None

    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None

    if (
        meta.get("embedder_tag") != embedder_tag
        or meta.get("window_tokens") != window_tokens
        or abs(float(meta.get("window_overlap", -1)) - window_overlap) > 1e-6
    ):
        return None  # stale embeddings — re-embed

    npz = np.load(npz_p, allow_pickle=True)
    convo_vec = npz["convo_vector"]
    window_vecs = npz["window_vectors"]
    window_texts = list(npz["window_texts"])

    labels: list[WindowLabel] = []
    cached_classifier = meta.get("classifier_tag")
    if classifier_tag is not None and cached_classifier == classifier_tag:
        try:
            label_objs = json.loads(str(npz["labels"]))
            for o in label_objs:
                labels.append(
                    WindowLabel(
                        stage=str(o.get("stage", "")),
                        outcome=str(o.get("outcome", "")),
                        confidence=float(o.get("confidence", 0.0)),
                        requires_human=bool(o.get("requires_human", False)),
                    )
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            labels = []

    return CachedEmbeddings(
        convo_id=convo_id,
        embedder_tag=meta.get("embedder_tag", embedder_tag),
        classifier_tag=cached_classifier,
        window_tokens=int(meta.get("window_tokens", window_tokens)),
        window_overlap=float(meta.get("window_overlap", window_overlap)),
        convo_vector=convo_vec,
        window_vectors=window_vecs,
        window_texts=window_texts,
        labels=labels,
    )


# ---------------------------------------------------------------------------
# Top-level: compute or fetch a CachedEmbeddings for one stream
# ---------------------------------------------------------------------------


def compute_embeddings(
    convo_id: str,
    turns,
    *,
    out_dir: Path,
    embedder_tag: str = DEFAULT_EMBEDDER_TAG,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    window_overlap: float = DEFAULT_WINDOW_OVERLAP,
    classifier_tag: str | None = None,
    host: str = OLLAMA_HOST,
    force: bool = False,
) -> CachedEmbeddings:
    """Compute (or fetch from cache) the convo + window vectors for one
    stream's turns. Labels are NOT computed here — call classify_windows()
    for those, then save_cache() to persist the combined result.

    `classifier_tag` is forwarded to load_cache so labels load too if the
    cached classifier matches; otherwise labels come back empty and the
    caller (e.g. compute_with_labels) re-runs classification."""
    if not force:
        cached = load_cache(
            out_dir,
            convo_id,
            embedder_tag=embedder_tag,
            window_tokens=window_tokens,
            window_overlap=window_overlap,
            classifier_tag=classifier_tag,
        )
        if cached is not None:
            return cached

    text = stream_text(turns)
    windows = windowize(text, window_tokens=window_tokens, overlap=window_overlap)
    convo_vec = embed_text(
        text[: window_tokens * 8],  # cheap concat-truncate for the convo-level vec
        model=embedder_tag,
        host=host,
    )
    win_vecs = embed_windows(windows, model=embedder_tag, host=host)

    return CachedEmbeddings(
        convo_id=convo_id,
        embedder_tag=embedder_tag,
        classifier_tag=None,
        window_tokens=window_tokens,
        window_overlap=window_overlap,
        convo_vector=convo_vec,
        window_vectors=win_vecs,
        window_texts=windows,
        labels=[],
    )


def compute_with_labels(
    convo_id: str,
    turns,
    *,
    out_dir: Path,
    embedder_tag: str = DEFAULT_EMBEDDER_TAG,
    classifier_tag: str = DEFAULT_CLASSIFIER_TAG,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    window_overlap: float = DEFAULT_WINDOW_OVERLAP,
    confidence_threshold: float = 0.7,
    host: str = OLLAMA_HOST,
    force: bool = False,
    progress: bool = False,
) -> CachedEmbeddings:
    """v0.4.1 — compute embeddings AND per-window stage labels for one
    stream. Persists the combined result.

    Honors v0.4 cache invalidation:
      - embedder/window-params change → full re-embed + re-classify
      - classifier_tag change ONLY → embeddings load from cache, only
        labels re-run. Independent invalidation.
      - everything matches → returns cache, zero LLM calls

    The `classifier_tag` propagates into compute_embeddings so its
    load_cache call returns labels too when they match. If labels are
    present in cache and match `classifier_tag`, this function is a
    no-op LLM-wise."""
    cached = compute_embeddings(
        convo_id,
        turns,
        out_dir=out_dir,
        embedder_tag=embedder_tag,
        window_tokens=window_tokens,
        window_overlap=window_overlap,
        classifier_tag=classifier_tag,
        host=host,
        force=force,
    )

    needs_classify = (
        not cached.has_labels()
        or cached.classifier_tag != classifier_tag
    )
    if not needs_classify:
        return cached

    # late import: keeps embeddings.py free of qwen3-instruct prompts
    from transcript_pipeline.window_classifier import classify_windows

    labels = classify_windows(
        cached.window_texts,
        model=classifier_tag,
        host=host,
        confidence_threshold=confidence_threshold,
        progress=progress,
    )
    cached.labels = labels
    cached.classifier_tag = classifier_tag
    save_cache(out_dir, cached)
    return cached
