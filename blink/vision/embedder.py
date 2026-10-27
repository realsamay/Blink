"""
ArcFace (MobileFaceNet w600k_mbf) ONNX embedder producing 512-dim facial vectors.
"""

from __future__ import annotations

import cv2
import numpy as np
import onnxruntime as ort
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
ARCFACE_PATH = MODEL_DIR / "w600k_mbf.onnx"


class ArcFaceEmbedder:
    def __init__(self, model_path: Optional[Path] = None):
        path = model_path or ARCFACE_PATH
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

    def compute_embedding(self, aligned_bgr: np.ndarray) -> np.ndarray:
        """Takes a 112x112 BGR image, preprocesses, and outputs normalized 512-dim embedding."""
        if aligned_bgr.shape[:2] != (112, 112):
            aligned_bgr = cv2.resize(aligned_bgr, (112, 112))

        # Convert BGR to RGB
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB)
        # Preprocessing: (pixel - 127.5) / 127.5
        blob = (rgb.astype(np.float32) - 127.5) / 127.5
        blob = np.transpose(blob, (2, 0, 1))
        blob = np.expand_dims(blob, axis=0)

        outputs = self.session.run([self.output_name], {self.input_name: blob})
        raw_emb = outputs[0][0]
        # L2-normalize
        norm = np.linalg.norm(raw_emb)
        if norm > 1e-6:
            return raw_emb / norm
        return raw_emb

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
        """Returns best match if composite score or individual pose matches within threshold."""
        if not scored:
            return None
        top_identity, centroid_sim, max_sim = scored[0]
        composite = 0.5 * centroid_sim + 0.5 * max_sim
        if composite >= threshold or centroid_sim >= threshold or max_sim >= max(threshold, 0.65):
            return (top_identity, centroid_sim, max_sim)
        return None
