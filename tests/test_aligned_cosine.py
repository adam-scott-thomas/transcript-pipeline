"""v0.4.1 — aligned_max_cosine: stage gate before cosine."""

import numpy as np
import pytest

from transcript_pipeline.embeddings import CachedEmbeddings, WindowLabel
from transcript_pipeline.temporal_weaver import aligned_max_cosine


def _emb(stages: list[str], confidences: list[float], vectors: np.ndarray) -> CachedEmbeddings:
    """Build a CachedEmbeddings with N windows, one stage per window."""
    return CachedEmbeddings(
        convo_id="test",
        embedder_tag="qwen3-embedding:8b",
        classifier_tag="qwen3:8b",
        window_tokens=2500,
        window_overlap=0.5,
        convo_vector=vectors.mean(axis=0),
        window_vectors=vectors,
        window_texts=[f"win{i}" for i in range(len(stages))],
        labels=[
            WindowLabel(stage=s, outcome="-", confidence=c, requires_human=c < 0.7)
            for s, c in zip(stages, confidences)
        ],
    )


def test_aligned_cosine_filters_to_target_stage():
    """Anchor has Audit + Build windows; candidate has Audit + Build windows.
    align_on_stage=Audit should ONLY consider Audit windows on both sides."""
    # Build vectors so Audit-vs-Audit is high and Build-vs-Build is high
    # but Audit-vs-Build is low (orthogonal).
    audit_axis = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    build_axis = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    anchor = _emb(
        stages=["Audit", "Build"],
        confidences=[0.9, 0.9],
        vectors=np.stack([audit_axis, build_axis]),
    )
    cand = _emb(
        stages=["Audit", "Build"],
        confidences=[0.9, 0.9],
        vectors=np.stack([audit_axis, build_axis]),
    )

    audit_sim, low = aligned_max_cosine(anchor, cand, "Audit")
    build_sim, _ = aligned_max_cosine(anchor, cand, "Build")
    review_sim, _ = aligned_max_cosine(anchor, cand, "Review")

    assert abs(audit_sim - 1.0) < 1e-5
    assert abs(build_sim - 1.0) < 1e-5
    assert review_sim == 0.0  # neither side has Review windows
    assert low is False


def test_aligned_cosine_does_not_use_off_stage_windows():
    """If anchor's Audit vector is identical to candidate's Build vector,
    align_on_stage=Audit should NOT pick that up — stage label gates."""
    same = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    other = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    anchor = _emb(
        stages=["Audit"],
        confidences=[0.9],
        vectors=np.stack([same]),
    )
    cand = _emb(
        stages=["Build"],  # has same vector but wrong stage
        confidences=[0.9],
        vectors=np.stack([same]),
    )
    sim, _ = aligned_max_cosine(anchor, cand, "Audit")
    assert sim == 0.0  # candidate has no Audit windows


def test_low_confidence_window_flags_alignment():
    """Per spec: requires_human=True windows still participate in alignment
    but the result is flagged."""
    vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)

    anchor = _emb(
        stages=["Audit"],
        confidences=[0.9],
        vectors=np.stack([vec]),
    )
    # candidate Audit window is shaky (conf < 0.7)
    cand = _emb(
        stages=["Audit"],
        confidences=[0.55],
        vectors=np.stack([vec]),
    )
    sim, low_conf = aligned_max_cosine(anchor, cand, "Audit")
    assert sim > 0.99
    assert low_conf is True


def test_no_labels_returns_zero():
    anchor = CachedEmbeddings(
        convo_id="a",
        embedder_tag="x",
        classifier_tag=None,
        window_tokens=2500,
        window_overlap=0.5,
        convo_vector=np.array([1.0, 0.0], dtype=np.float32),
        window_vectors=np.array([[1.0, 0.0]], dtype=np.float32),
        window_texts=["w"],
        labels=[],  # no labels
    )
    cand = anchor
    sim, low = aligned_max_cosine(anchor, cand, "Audit")
    assert sim == 0.0
    assert low is False
