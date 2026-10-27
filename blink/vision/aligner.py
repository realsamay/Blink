"""
Face aligner solving similarity transforms to warp faces into canonical 112x112 ArcFace pose.
"""

from __future__ import annotations

import cv2
import numpy as np
from typing import Optional, Tuple
from blink.vision.detector import DetectedFace


class FaceAligner:
    OUTPUT_SIZE = 112

    # Standard 112x112 ArcFace reference landmark points:
    # [left_eye, right_eye, nose, left_mouth, right_mouth]
    REFERENCE_POINTS = np.array([
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ], dtype=np.float32)

    @classmethod
    def align(cls, face: DetectedFace, image: np.ndarray) -> Optional[np.ndarray]:
        """Aligns face using 5-point similarity transformation."""
        src_points = face.landmarks_5pt.astype(np.float32)
        # Estimate 2D similarity transformation matrix (rotation, translation, uniform scale)
        M, inliers = cv2.estimateAffinePartial2D(src_points, cls.REFERENCE_POINTS)
        if M is None:
            # Fallback to padded crop
            x, y, w, h = face.bbox
            img_h, img_w = image.shape[:2]
            pad = int(w * 0.2)
            x1 = max(0, x - pad)
            y1 = max(0, y - pad)
            x2 = min(img_w, x + w + pad)
            y2 = min(img_h, y + h + pad)
            if x2 <= x1 or y2 <= y1:
                return None
            crop = image[y1:y2, x1:x2]
            return cv2.resize(crop, (cls.OUTPUT_SIZE, cls.OUTPUT_SIZE))

        warped = cv2.warpAffine(
            image,
            M,
            (cls.OUTPUT_SIZE, cls.OUTPUT_SIZE),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101
        )
        return warped
