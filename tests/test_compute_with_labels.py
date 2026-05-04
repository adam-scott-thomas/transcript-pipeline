"""v0.4.1 — compute_with_labels honors independent cache invalidation."""

from pathlib import Path
from unittest.mock import patch

import numpy as np

from transcript_pipeline.embeddings import (
    CachedEmbeddings,
    WindowLabel,
    compute_with_labels,
    save_cache,
)


def _seed_cache(out_dir: Path, convo_id: str, classifier_tag: str | None, with_labels: bool):
    """Pre-populate the cache so compute_with_labels has something to load."""
    labels = []
    if with_labels:
        labels = [
            WindowLabel(stage="Build", outcome="x", confidence=0.9, requires_human=False),
        ]
    c = CachedEmbeddings(
        convo_id=convo_id,
        embedder_tag="qwen3-embedding:8b",
        classifier_tag=classifier_tag,
        window_tokens=2500,
        window_overlap=0.5,
        convo_vector=np.zeros(4096, dtype=np.float32),
        window_vectors=np.zeros((1, 4096), dtype=np.float32),
        window_texts=["one window"],
        labels=labels,
    )
    save_cache(out_dir, c)


def test_no_op_when_cache_has_matching_classifier_and_labels(tmp_path):
    _seed_cache(tmp_path, "c1", classifier_tag="qwen3:8b", with_labels=True)

    with patch("transcript_pipeline.window_classifier.classify_windows") as mock_cls:
        result = compute_with_labels(
            "c1",
            turns=[],
            out_dir=tmp_path,
            embedder_tag="qwen3-embedding:8b",
            classifier_tag="qwen3:8b",
            window_tokens=2500,
            window_overlap=0.5,
        )
        # cache hit; classifier should NOT be invoked
        mock_cls.assert_not_called()

    assert result.has_labels()
    assert result.labels[0].stage == "Build"


def test_reclassifies_when_classifier_tag_changes(tmp_path):
    """v0.4 invariant: changing classifier preserves embeddings, drops labels.
    v0.4.1: compute_with_labels then re-runs classification."""
    _seed_cache(tmp_path, "c2", classifier_tag="qwen3:8b", with_labels=True)

    fake_labels = [
        WindowLabel(stage="Audit", outcome="trace", confidence=0.91, requires_human=False),
    ]
    with patch(
        "transcript_pipeline.window_classifier.classify_windows",
        return_value=fake_labels,
    ) as mock_cls:
        result = compute_with_labels(
            "c2",
            turns=[],
            out_dir=tmp_path,
            embedder_tag="qwen3-embedding:8b",
            classifier_tag="qwen3:14b",  # different classifier
            window_tokens=2500,
            window_overlap=0.5,
        )
        mock_cls.assert_called_once()

    assert result.classifier_tag == "qwen3:14b"
    assert result.labels[0].stage == "Audit"


def test_reclassifies_when_cache_missing_labels(tmp_path):
    """If embeddings are cached but labels are empty (e.g. previous run was
    --no-classify), compute_with_labels should fill them in."""
    _seed_cache(tmp_path, "c3", classifier_tag=None, with_labels=False)

    fake_labels = [
        WindowLabel(stage="Ship", outcome="deployed", confidence=0.95, requires_human=False),
    ]
    with patch(
        "transcript_pipeline.window_classifier.classify_windows",
        return_value=fake_labels,
    ) as mock_cls:
        result = compute_with_labels(
            "c3",
            turns=[],
            out_dir=tmp_path,
            embedder_tag="qwen3-embedding:8b",
            classifier_tag="qwen3:8b",
            window_tokens=2500,
            window_overlap=0.5,
        )
        mock_cls.assert_called_once()

    assert result.classifier_tag == "qwen3:8b"
    assert len(result.labels) == 1
