"""
ArcFace (MobileFaceNet w600k_mbf) ONNX embedder producing 512-dim facial vectors.
"""

from __future__ import annotations

import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from blink.core.paths import get_model_path

ARCFACE_PATH = get_model_path("w600k_mbf.onnx")


class ArcFaceEmbedder:
    def __init__(self, model_path: Optional[Path] = None):
        path = model_path or get_model_path("w600k_mbf.onnx")
        assert path.exists(), f"ArcFace ONNX model missing at {path}"
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

    def compute_embedding(self, aligned_bgr: np.ndarray, flip_augment: bool = True) -> np.ndarray:
        """Takes a 112x112 BGR image, preprocesses, and outputs normalized 512-dim embedding.
        Uses horizontal flip augmentation by default to guarantee 100% mirror invariance.
        """
        if aligned_bgr.shape[:2] != (112, 112):
            aligned_bgr = cv2.resize(aligned_bgr, (112, 112))

        def _forward(bgr_crop: np.ndarray) -> np.ndarray:
            rgb = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2RGB)
            blob = (rgb.astype(np.float32) - 127.5) / 127.5
            blob = np.transpose(blob, (2, 0, 1))
            blob = np.expand_dims(blob, axis=0)
            outputs = self.session.run([self.output_name], {self.input_name: blob})
            raw = outputs[0][0]
            norm = np.linalg.norm(raw)
            return (raw / norm) if norm > 1e-6 else raw

        emb = _forward(aligned_bgr)
        if flip_augment:
            flipped_bgr = cv2.flip(aligned_bgr, 1)
            emb_flip = _forward(flipped_bgr)
            combined = emb + emb_flip
            c_norm = np.linalg.norm(combined)
            return (combined / c_norm) if c_norm > 1e-6 else combined
        return emb

    @staticmethod
    def cosine_similarity(v1: np.ndarray, v2: np.ndarray) -> float:
        return float(np.dot(v1, v2))

    @staticmethod
    def average_embeddings(embeddings: List[np.ndarray]) -> np.ndarray:
        """Averages multiple face embeddings into a single centroid vector."""
        if not embeddings:
            return np.zeros(512, dtype=np.float32)
        mean_vec = np.mean(embeddings, axis=0)
        norm = np.linalg.norm(mean_vec)
        if norm > 1e-6:
            return mean_vec / norm
        return mean_vec

    @classmethod
    def score_identities(
        cls,
        embedding: np.ndarray,
        identities: List[Dict[str, Any]]
    ) -> List[Tuple[Dict[str, Any], float, float]]:
        """
        Scores an embedding against enrolled identities.
        Returns sorted list of (identity, centroid_similarity, max_sample_similarity).
        """
        results = []
        for identity in identities:
            if not identity.get("is_enabled", True):
                continue
            samples = identity.get("samples", [])
            if not samples:
                continue

            sample_embs = [np.array(s["embedding"], dtype=np.float32) for s in samples]
            template = cls.average_embeddings(sample_embs)
            centroid_sim = cls.cosine_similarity(embedding, template)

            sims = [cls.cosine_similarity(embedding, s) for s in sample_embs]
            max_sim = max(sims) if sims else centroid_sim

            results.append((identity, centroid_sim, max_sim))

        results.sort(key=lambda item: item[1], reverse=True)
        return results

    @classmethod
    def best_match(
        cls,
        scored: List[Tuple[Dict[str, Any], float, float]],
        threshold: float
    ) -> Optional[Tuple[Dict[str, Any], float, float]]:
        """Returns best match if centroid and composite similarity strictly satisfy threshold."""
        if not scored:
            return None
        top_identity, centroid_sim, max_sim = scored[0]

        # Strict biometric safety floor: ArcFace MobileFaceNet produces ~0.35-0.52 for random strangers.
        # Under no circumstance allow a match threshold below 0.60.
        effective_thresh = max(float(threshold), 0.60)
        composite = 0.60 * centroid_sim + 0.40 * max_sim

        # Both the overall identity centroid and composite score MUST pass the threshold,
        # and individual max pose similarity must not fall below 0.60.
        if centroid_sim >= effective_thresh and composite >= effective_thresh and max_sim >= 0.60:
            return (top_identity, centroid_sim, max_sim)
        return None
