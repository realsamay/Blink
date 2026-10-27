"""
Face detection and landmark extraction using OpenCV YuNet and InsightFace 2D-106.
Provides 5-point alignment landmarks, 106-point fine mesh, head pose, and continuity tracking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np
import onnxruntime as ort

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
YUNET_PATH = MODEL_DIR / "face_detection_yunet.onnx"
LANDMARKS_106_PATH = MODEL_DIR / "2d106det.onnx"


@dataclass
class DetectedFace:
    bbox: Tuple[int, int, int, int]  # x, y, w, h in pixel coordinates
    score: float
    landmarks_5pt: np.ndarray        # (5, 2): [left_eye, right_eye, nose, left_mouth, right_mouth]
    landmarks_106pt: Optional[np.ndarray] = None  # (106, 2)
    yaw: float = 0.0                # in radians
    pitch: float = 0.0
    roll: float = 0.0

    @property
    def center(self) -> Tuple[float, float]:
        x, y, w, h = self.bbox
        return (x + w / 2.0, y + h / 2.0)

    @property
    def area(self) -> float:
        return float(self.bbox[2] * self.bbox[3])


class FaceDetector:
    def __init__(self, score_threshold: float = 0.40, nms_threshold: float = 0.3):
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self._current_input_size = (640, 480)

        cv2.setNumThreads(2)

        assert YUNET_PATH.exists(), f"YuNet model missing at {YUNET_PATH}"
        self.detector = cv2.FaceDetectorYN.create(
            str(YUNET_PATH),
            "",
            self._current_input_size,
            score_threshold=score_threshold,
            nms_threshold=nms_threshold,
            top_k=5000,
        )

        # 106-point landmark detector for liveness and geometry
        self.landmark_session: Optional[ort.InferenceSession] = None
        if LANDMARKS_106_PATH.exists():
            try:
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                opts.inter_op_num_threads = 1
                opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                opts.enable_cpu_mem_arena = False
                self.landmark_session = ort.InferenceSession(
                    str(LANDMARKS_106_PATH),
                    sess_options=opts,
                    providers=["CPUExecutionProvider"]
                )
            except Exception as e:
                print(f"[FaceDetector] Could not load 106-landmark model: {e}")

    def detect_faces(self, image: np.ndarray, compute_106_landmarks: bool = True) -> List[DetectedFace]:
        h, w = image.shape[:2]
        if (w, h) != self._current_input_size:
            self._current_input_size = (w, h)
            self.detector.setInputSize((w, h))

        status, raw_faces = self.detector.detect(image)
        if raw_faces is None or len(raw_faces) == 0:
            return []

        results: List[DetectedFace] = []
        for face_data in raw_faces:
            x, y, fw, fh = map(int, face_data[0:4])
            score = float(face_data[14])

            # Extract 5 landmarks: right_eye, left_eye, nose_tip, right_mouth, left_mouth
            # Convert to standard canonical order: left_eye, right_eye, nose, left_mouth, right_mouth (image space)
            re_x, re_y = face_data[4], face_data[5]
            le_x, le_y = face_data[6], face_data[7]
            nt_x, nt_y = face_data[8], face_data[9]
            rcm_x, rcm_y = face_data[10], face_data[11]
            lcm_x, lcm_y = face_data[12], face_data[13]

            # In image coordinates, left vs right on screen:
            eye1 = np.array([re_x, re_y], dtype=np.float32)
            eye2 = np.array([le_x, le_y], dtype=np.float32)
            left_eye = eye1 if eye1[0] <= eye2[0] else eye2
            right_eye = eye2 if eye1[0] <= eye2[0] else eye1

            mouth1 = np.array([rcm_x, rcm_y], dtype=np.float32)
            mouth2 = np.array([lcm_x, lcm_y], dtype=np.float32)
            left_mouth = mouth1 if mouth1[0] <= mouth2[0] else mouth2
            right_mouth = mouth2 if mouth1[0] <= mouth2[0] else mouth1

            nose = np.array([nt_x, nt_y], dtype=np.float32)
            landmarks_5pt = np.stack([left_eye, right_eye, nose, left_mouth, right_mouth])

            # Estimate yaw and roll
            dx = right_eye[0] - left_eye[0]
            dy = right_eye[1] - left_eye[1]
            roll = math.atan2(dy, dx)
            interocular = math.hypot(dx, dy)
            eye_mid_x = (left_eye[0] + right_eye[0]) / 2.0
            nose_offset = nose[0] - eye_mid_x
            yaw = (nose_offset / max(interocular, 1e-4)) * (math.pi / 2.0)

            detected = DetectedFace(
                bbox=(x, y, fw, fh),
                score=score,
                landmarks_5pt=landmarks_5pt,
                yaw=float(yaw),
                roll=float(roll),
            )

            # Compute 106-point landmarks if requested
            if compute_106_landmarks and self.landmark_session is not None:
                detected.landmarks_106pt = self._extract_106_landmarks(image, x, y, fw, fh)

            results.append(detected)

        return results

    def _extract_106_landmarks(self, image: np.ndarray, x: int, y: int, w: int, h: int) -> Optional[np.ndarray]:
        img_h, img_w = image.shape[:2]
        # Pad face region
        pad_x = int(w * 0.15)
        pad_y = int(h * 0.15)
        x1 = max(0, x - pad_x)
        y1 = max(0, y - pad_y)
        x2 = min(img_w, x + w + pad_x)
        y2 = min(img_h, y + h + pad_y)
        if x2 <= x1 or y2 <= y1:
            return None

        crop = image[y1:y2, x1:x2]
        crop_h, crop_w = crop.shape[:2]
        resized = cv2.resize(crop, (192, 192))
        blob = resized.astype(np.float32)
        blob = np.transpose(blob, (2, 0, 1))
        blob = np.expand_dims(blob, axis=0)

        outputs = self.landmark_session.run(None, {"data": blob})
        raw_pts = outputs[0].reshape(-1, 2)  # 106 points in 192x192

        # Scale back to original image coordinates
        scale_x = crop_w / 192.0
        scale_y = crop_h / 192.0
        orig_pts = raw_pts.copy()
        orig_pts[:, 0] = raw_pts[:, 0] * scale_x + x1
        orig_pts[:, 1] = raw_pts[:, 1] * scale_y + y1
        return orig_pts

    @staticmethod
    def select_dominant_face(
        faces: List[DetectedFace],
        frame_width: int,
        previous_bbox: Optional[Tuple[int, int, int, int]] = None,
        min_width_fraction: float = 0.04,
        continuity_tolerance: float = 0.35,
    ) -> Optional[DetectedFace]:
        """Picks user face with wide distance tolerance (from 20cm to 2m), stabilizing tracking."""
        candidates = [f for f in faces if (f.bbox[2] / float(frame_width)) >= min_width_fraction]
        if not candidates:
            return None

        if previous_bbox is not None:
            prev_cx = previous_bbox[0] + previous_bbox[2] / 2.0
            prev_cy = previous_bbox[1] + previous_bbox[3] / 2.0

            def distance_to_prev(f: DetectedFace) -> float:
                cx, cy = f.center
                return math.hypot(cx - prev_cx, cy - prev_cy) / float(frame_width)

            nearest = min(candidates, key=distance_to_prev)
            if distance_to_prev(nearest) < continuity_tolerance:
                return nearest

        # Fallback to largest face
        return max(candidates, key=lambda f: f.area)
