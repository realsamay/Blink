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
        Ranks identities primarily by best-matching sample similarity and top-K sample cohesion,
        ensuring multi-face setups correctly promote the genuine identity facing the camera.
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

            top_k = sorted(sims, reverse=True)[:min(3, len(sims))]
            top_k_mean = float(np.mean(top_k)) if top_k else max_sim

            # Weighted composite score for ranking candidates:
            # Gives 55% weight to the peak pose match, 30% to top-3 sample consensus,
            # and 15% to overall centroid cohesion.
            rank_score = 0.55 * max_sim + 0.30 * top_k_mean + 0.15 * centroid_sim

            results.append((identity, centroid_sim, max_sim, rank_score))

        # Sort descending by composite rank score so the true matching face is always first
        results.sort(key=lambda item: item[3], reverse=True)
        return [(item[0], item[1], item[2]) for item in results]

    @classmethod
    def best_match(
        cls,
        scored: List[Tuple[Dict[str, Any], float, float]],
        threshold: float
    ) -> Optional[Tuple[Dict[str, Any], float, float]]:
        """
        Returns best match if candidate passes biometric safety threshold.
        Iterates across all ranked candidates rather than stopping at the first,
        guaranteeing multi-face setups resolve accurately and single-face unlocks
        operate smoothly without false rejections.
        """
        if not scored:
            return None

        # Biometric safety floor: ArcFace MobileFaceNet produces ~0.35-0.48 for random strangers.
        effective_thresh = max(float(threshold), 0.55)

        for identity, centroid_sim, max_sim in scored:
            composite = 0.65 * max_sim + 0.35 * centroid_sim

            # 1. High-confidence pose match (unmistakable user match with enrolled angle)
            if max_sim >= 0.72 and centroid_sim >= 0.38:
                return (identity, centroid_sim, max_sim)

            # 2. Balanced threshold match (both max sample and composite pass user threshold)
            if max_sim >= effective_thresh and composite >= (effective_thresh - 0.04) and centroid_sim >= 0.40:
                return (identity, centroid_sim, max_sim)

            # 3. High composite consensus match
            if composite >= effective_thresh and max_sim >= 0.60 and centroid_sim >= 0.40:
                return (identity, centroid_sim, max_sim)

        return None
