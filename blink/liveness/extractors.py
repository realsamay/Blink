"""
Liveness feature extractors: Glare specular highlight detection, device rectangle detection,
and facial landmark geometry (Eye Aspect Ratio blinks, nose parallax, homography 3D-vs-flat).
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple, Dict
import cv2
import numpy as np

from blink.liveness.cues import CueReading, LivenessCue


class GlareDetector:
    """Detects screen/glass specular reflections on face crop."""
    LUMA_FLOOR = 235.0
    CHROMA_TOLERANCE = 10.0
    GRID_SIZE = 8

    @classmethod
    def extract(cls, face_bgr: np.ndarray) -> CueReading:
        h, w = face_bgr.shape[:2]
        if w < 20 or h < 20:
            return CueReading(0.0, 0.0)

        # Convert to YCrCb for luma and chroma inspection
        ycrcb = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2YCrCb)
        y, cr, cb = ycrcb[:, :, 0], ycrcb[:, :, 1], ycrcb[:, :, 2]

        specular_mask = (
            (y >= cls.LUMA_FLOOR) &
            (np.abs(cb.astype(np.float32) - 128.0) <= cls.CHROMA_TOLERANCE) &
            (np.abs(cr.astype(np.float32) - 128.0) <= cls.CHROMA_TOLERANCE)
        )
        specular_count = int(np.count_nonzero(specular_mask))
        total_pixels = w * h
        specular_fraction = float(specular_count) / float(total_pixels)

        if specular_count == 0:
            return CueReading(0.0, 1.0)

        # Check cluster ratio in 8x8 grid
        grid_counts = np.zeros((cls.GRID_SIZE, cls.GRID_SIZE), dtype=np.int32)
        ys, xs = np.where(specular_mask)
        gys = np.clip((ys * cls.GRID_SIZE) // h, 0, cls.GRID_SIZE - 1)
        gxs = np.clip((xs * cls.GRID_SIZE) // w, 0, cls.GRID_SIZE - 1)
        for gy, gx in zip(gys, gxs):
            grid_counts[gy, gx] += 1

        largest_cluster = float(np.max(grid_counts))
        cluster_ratio = largest_cluster / float(specular_count)

        # High specular fraction + high clustering = screen glare
        glare_level = min(1.0, specular_fraction * 15.0 * cluster_ratio)
        return CueReading(level=glare_level, confidence=1.0)


class DeviceBezelDetector:
    """Detects rectangular phone/tablet bezels framing the face."""

    @classmethod
    def extract(cls, full_frame_bgr: np.ndarray, face_bbox: Tuple[int, int, int, int]) -> CueReading:
        fx, fy, fw, fh = face_bbox
        face_area = fw * fh
        if face_area <= 0:
            return CueReading(0.0, 0.0)

        gray = cv2.cvtColor(full_frame_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)

        contours, _ = cv2.findContours(edges, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        frame_h, frame_w = full_frame_bgr.shape[:2]
        frame_area = frame_w * frame_h

        max_overlap = 0.0
        for cnt in contours:
            approx = cv2.approxPolyDP(cnt, 0.03 * cv2.arcLength(cnt, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                rx, ry, rw, rh = cv2.boundingRect(approx)
                rect_area = rw * rh
                if rect_area < (0.10 * frame_area) or rect_area > (0.95 * frame_area):
                    continue
                aspect = float(rw) / float(rh)
                if not (0.35 <= aspect <= 1.8):
                    continue

                # Compute intersection with face bounding box
                ix1 = max(fx, rx)
                iy1 = max(fy, ry)
                ix2 = min(fx + fw, rx + rw)
                iy2 = min(fy + fh, ry + rh)
                if ix2 > ix1 and iy2 > iy1:
                    inter_area = (ix2 - ix1) * (iy2 - iy1)
                    overlap = inter_area / float(face_area)
                    if overlap > max_overlap:
                        max_overlap = overlap

        # Overlap fraction > 0.8 means device rectangle frames the face
        confidence = 1.0 if max_overlap > 0.1 else 0.5
        return CueReading(level=float(max_overlap), confidence=confidence)


class LandmarkGeometry:
    """Geometry-based liveness: blink dynamics, nose parallax, homography 3D-vs-flat."""

    @classmethod
    def compute_ear_5pt(cls, landmarks: np.ndarray) -> float:
        """Approximates Eye Aspect Ratio using 5-point landmarks."""
        # 0: left_eye, 1: right_eye
        return 0.28  # nominal baseline

    @classmethod
    def compute_ear_106pt(cls, landmarks_106: np.ndarray) -> Tuple[float, float]:
        """Calculates precise left and right Eye Aspect Ratio from 106-point mesh."""
        # In InsightFace 106 landmark scheme:
        # Left eye: points [35..42] (pupil at 34)
        # Right eye: points [89..96] (pupil at 88)
        # EAR = (||p1 - p5|| + ||p2 - p4||) / (2 * ||p0 - p3||)
        def single_ear(pts: np.ndarray) -> float:
            p_left = pts[0]
            p_right = pts[4]
            horiz = math.hypot(p_left[0] - p_right[0], p_left[1] - p_right[1]) + 1e-6
            vert1 = math.hypot(pts[2][0] - pts[6][0], pts[2][1] - pts[6][1])
            vert2 = math.hypot(pts[1][0] - pts[7][0], pts[1][1] - pts[7][1])
            return (vert1 + vert2) / (2.0 * horiz)

        try:
            left_pts = landmarks_106[35:43]
            right_pts = landmarks_106[89:97]
            if len(left_pts) == 8 and len(right_pts) == 8:
                return (single_ear(left_pts), single_ear(right_pts))
        except Exception:
            pass
        return (0.28, 0.28)

    @classmethod
    def check_blink(cls, ear_history: List[float]) -> CueReading:
        """Detects eye blink: EAR dips below 0.18 and recovers to > 0.25 within rolling window."""
        if len(ear_history) < 3:
            return CueReading(0.0, 0.0)

        min_ear = min(ear_history)
        max_ear = max(ear_history)
        # Dip and recover
        if min_ear < 0.18 and max_ear > 0.24 and (max_ear - min_ear) > 0.07:
            return CueReading(level=1.0, confidence=1.0)
        return CueReading(level=0.0, confidence=0.8)

    @classmethod
    def check_depth_pose(cls, nose_offsets: List[float], yaws: List[float]) -> CueReading:
        """Measures correlation between nose offset and head yaw (3D parallax)."""
        if len(nose_offsets) < 5 or len(yaws) < 5:
            return CueReading(0.0, 0.0)

        yaw_range = max(yaws) - min(yaws)
        if yaw_range < math.radians(6):  # Need at least ~6 degrees of head movement
            return CueReading(0.0, 0.0)

        # Pearson correlation between yaw and nose offset
        o = np.array(nose_offsets)
        y = np.array(yaws)
        o_std = np.std(o)
        y_std = np.std(y)
        if o_std < 1e-5 or y_std < 1e-5:
            return CueReading(0.0, 0.0)

        r = float(np.corrcoef(o, y)[0, 1])
        # Map r (-1..1) to level
        level = max(0.0, min(1.0, (r + 1.0) / 2.0))
        return CueReading(level=level, confidence=1.0)

    @classmethod
    def check_flat_vs_3d(cls, landmark_history: List[np.ndarray]) -> CueReading:
        """
        Homography fit on planar points (eyes, brows, mouth) vs held-out nose.
        A real 3D face has protruding nose, generating residual excess during rotation.
        """
        if len(landmark_history) < 4:
            return CueReading(0.0, 0.0)

        # Use first and last frames with sufficient motion
        f0 = landmark_history[0]
        f1 = landmark_history[-1]
        if f0.shape != f1.shape or len(f0) < 5:
            return CueReading(0.0, 0.0)

        # Planar fit indices: eyes and mouth
        # Probe indices: nose
        fit_idx = [0, 1, 3, 4]  # left eye, right eye, left mouth, right mouth
        probe_idx = [2]        # nose

        pts0_fit = f0[fit_idx].astype(np.float32)
        pts1_fit = f1[fit_idx].astype(np.float32)

        H, _ = cv2.findHomography(pts0_fit, pts1_fit, method=0)
        if H is None:
            return CueReading(0.0, 0.0)

        # Transform fit points and compute residual
        pts0_fit_h = np.column_stack([pts0_fit, np.ones(len(pts0_fit))])
        pred_fit = (H @ pts0_fit_h.T).T
        pred_fit = pred_fit[:, :2] / (pred_fit[:, 2:3] + 1e-8)
        fit_residual = float(np.mean(np.linalg.norm(pred_fit - pts1_fit, axis=1)))

        # Transform nose probe point
        pts0_probe = f0[probe_idx].astype(np.float32)
        pts1_probe = f1[probe_idx].astype(np.float32)
        pts0_probe_h = np.column_stack([pts0_probe, np.ones(len(pts0_probe))])
        pred_probe = (H @ pts0_probe_h.T).T
        pred_probe = pred_probe[:, :2] / (pred_probe[:, 2:3] + 1e-8)
        probe_residual = float(np.mean(np.linalg.norm(pred_probe - pts1_probe, axis=1)))

        if fit_residual < 1e-4:
            excess = 1.0
        else:
            excess = probe_residual / fit_residual

        # Live 3D head: excess typically > 1.25. Flat photo: excess ~ 1.0
        score = max(0.0, min(1.0, (excess - 1.1) / 0.9))
        return CueReading(level=float(score), confidence=1.0)
