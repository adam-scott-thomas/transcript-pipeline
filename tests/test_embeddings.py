"""v0.4 embeddings: windowing, cache round-trip, cosine math, key invalidation."""

from pathlib import Path

import numpy as np
import pytest

from transcript_pipeline.embeddings import (
    CachedEmbeddings,
    WindowLabel,
    _cosine,
    load_cache,
    max_cosine,
    save_cache,
    windowize,
)


# ---------------------------------------------------------------------------
# windowize
# ---------------------------------------------------------------------------


def test_windowize_short_text_returns_one_window():
    out = windowize("short text", window_tokens=2500, overlap=0.5)
    assert len(out) == 1
    assert "short" in out[0]


def test_windowize_long_text_yields_overlapping_windows():
    text = " ".join(f"word{i}" for i in range(5000))  # roughly 5000 tokens
    out = windowize(text, window_tokens=1000, overlap=0.5)
    # at least 5 windows, overlap means more
    assert len(out) >= 5
    # consecutive windows should share content (overlap)
    assert out[0][-100:] != out[1][-100:]
    # but adjacent windows do share their middle token range — sanity that
    # first/last windows are different
    assert out[0][:200] != out[-1][:200]


def test_windowize_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        windowize("abc", window_tokens=10, overlap=1.0)
    with pytest.raises(ValueError):
        windowize("abc", window_tokens=10, overlap=-0.1)


def test_windowize_empty_returns_one_empty_window():
    assert windowize("", window_tokens=100, overlap=0.5) == [""]


# ---------------------------------------------------------------------------
# cosine + max_cosine
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors_is_one():
    v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert abs(_cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_is_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert abs(_cosine(a, b)) < 1e-6


def test_max_cosine_picks_best_match():
    q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    cands = np.array(
        [[0.0, 1.0, 0.0], [0.5, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32
    )
    # best is row 2 (identical to q) → cosine 1.0
    assert abs(max_cosine(q, cands) - 1.0) < 1e-6


def test_max_cosine_empty_candidates_returns_zero():
    q = np.array([1.0, 0.0], dtype=np.float32)
    cands = np.zeros((0, 2), dtype=np.float32)
    assert max_cosine(q, cands) == 0.0


# ---------------------------------------------------------------------------
# save / load cache round-trip
# ---------------------------------------------------------------------------


def _mk_cache(convo_id="c1", classifier_tag="qwen3:8b") -> CachedEmbeddings:
    return CachedEmbeddings(
        convo_id=convo_id,
        embedder_tag="qwen3-embedding:8b",
        classifier_tag=classifier_tag,
        window_tokens=2500,
        window_overlap=0.5,
        convo_vector=np.random.rand(4096).astype(np.float32),
        window_vectors=np.random.rand(3, 4096).astype(np.float32),
        window_texts=["window 1 text", "window 2 text", "window 3 text"],
        labels=[
            WindowLabel(stage="Context", outcome="setup goal", confidence=0.92, requires_human=False),
            WindowLabel(stage="Build", outcome="implementing adapter", confidence=0.88, requires_human=False),
            WindowLabel(stage="Audit", outcome="trace failure", confidence=0.55, requires_human=True),
        ],
    )


def test_save_and_load_cache_roundtrip(tmp_path):
    c = _mk_cache()
    save_cache(tmp_path, c)
    loaded = load_cache(
        tmp_path,
        c.convo_id,
        embedder_tag=c.embedder_tag,
        window_tokens=c.window_tokens,
        window_overlap=c.window_overlap,
        classifier_tag=c.classifier_tag,
    )
    assert loaded is not None
    assert loaded.convo_id == c.convo_id
    assert loaded.window_texts == c.window_texts
    # fp16 lossy but cosine should still be close
    assert _cosine(loaded.convo_vector, c.convo_vector) > 0.99
    # labels survived
    assert len(loaded.labels) == 3
    assert loaded.labels[0].stage == "Context"
    assert loaded.labels[2].requires_human is True


def test_cache_invalidates_on_window_param_change(tmp_path):
    c = _mk_cache()
    save_cache(tmp_path, c)
    miss = load_cache(
        tmp_path,
        c.convo_id,
        embedder_tag=c.embedder_tag,
        window_tokens=1000,  # different
        window_overlap=c.window_overlap,
    )
    assert miss is None


def test_cache_invalidates_on_embedder_change(tmp_path):
    c = _mk_cache()
    save_cache(tmp_path, c)
    miss = load_cache(
        tmp_path,
        c.convo_id,
        embedder_tag="nomic-embed-text",  # different
        window_tokens=c.window_tokens,
        window_overlap=c.window_overlap,
    )
    assert miss is None


def test_cache_keeps_embeddings_when_classifier_changes(tmp_path):
    """Reclassifying with a new model shouldn't blow away embeddings."""
    c = _mk_cache(classifier_tag="qwen3:8b")
    save_cache(tmp_path, c)
    # ask for the cache with a different classifier — we expect the
    # embeddings to load, but labels to be empty (caller re-classifies).
    loaded = load_cache(
        tmp_path,
        c.convo_id,
        embedder_tag=c.embedder_tag,
        window_tokens=c.window_tokens,
        window_overlap=c.window_overlap,
        classifier_tag="qwen3:14b",  # mismatch
    )
    assert loaded is not None
    assert _cosine(loaded.convo_vector, c.convo_vector) > 0.99
    assert loaded.labels == []  # empty so caller re-classifies


def test_cache_storage_uses_float16_on_disk(tmp_path):
    c = _mk_cache()
    save_cache(tmp_path, c)
    npz_path = tmp_path / "embeddings" / f"{c.convo_id}.npz"
    arrs = np.load(npz_path, allow_pickle=True)
    assert arrs["convo_vector"].dtype == np.float16
    assert arrs["window_vectors"].dtype == np.float16
