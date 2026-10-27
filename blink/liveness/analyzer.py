"""
LivenessAnalyzer maintains a rolling window of frames and drives the liveness state machine.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional
import numpy as np

from blink.liveness.cues import (
    CueReading,
    CueState,
    LivenessCue,
    LivenessCueRole,
    LivenessDecision,
    LivenessDecisionKind,
    LivenessTuning,
)
from blink.liveness.extractors import GlareDetector, DeviceBezelDetector, LandmarkGeometry
from blink.vision.detector import DetectedFace


class LivenessAnalyzer:
    def __init__(self, mode: str = "light", window_seconds: float = 2.0):
        self.mode = mode  # "light" | "heavy"
        self.window_seconds = window_seconds
        self.tuning = LivenessTuning()

        self._frame_times: List[float] = []
        self._ear_history: List[float] = []
        self._nose_offsets: List[float] = []
        self._yaws: List[float] = []
        self._landmarks_history: List[np.ndarray] = []

        self.cue_states: Dict[LivenessCue, CueState] = {
            cue: CueState() for cue in LivenessCue
        }
        self.total_frames_observed = 0

    def reset(self) -> None:
        self._frame_times.clear()
        self._ear_history.clear()
        self._nose_offsets.clear()
        self._yaws.clear()
        self._landmarks_history.clear()
        self.total_frames_observed = 0
        self.cue_states = {cue: CueState() for cue in LivenessCue}

    def observe(
        self,
        full_frame_bgr: np.ndarray,
        face: DetectedFace,
        face_crop_bgr: Optional[np.ndarray] = None
    ) -> LivenessDecision:
        now = time.time()
        self._frame_times.append(now)
        self.total_frames_observed += 1

        # Prune records older than window_seconds
        cutoff = now - self.window_seconds
        while self._frame_times and self._frame_times[0] < cutoff:
            idx = 0
            self._frame_times.pop(0)
            if idx < len(self._ear_history):
                self._ear_history.pop(0)
            if idx < len(self._nose_offsets):
                self._nose_offsets.pop(0)
            if idx < len(self._yaws):
                self._yaws.pop(0)
            if idx < len(self._landmarks_history):
                self._landmarks_history.pop(0)

        # 1. Glare cue (DENY)
        if face_crop_bgr is not None:
            glare_reading = GlareDetector.extract(face_crop_bgr)
            self._update_cue(LivenessCue.GLOSS_GLARE, glare_reading)

        # 2. Device bezel cue (DENY)
        device_reading = DeviceBezelDetector.extract(full_frame_bgr, face.bbox)
        self._update_cue(LivenessCue.DEVICE_DETECTED, device_reading)

        # 3. Geometry records (CONFIRM)
        # EAR for Blink
        if face.landmarks_106pt is not None:
            left_ear, right_ear = LandmarkGeometry.compute_ear_106pt(face.landmarks_106pt)
            avg_ear = (left_ear + right_ear) / 2.0
            self._ear_history.append(avg_ear)
            blink_reading = LandmarkGeometry.check_blink(self._ear_history)
            self._update_cue(LivenessCue.BLINK, blink_reading)

        # Nose offset and yaw for Depth/pose
        left_eye, right_eye, nose = face.landmarks_5pt[0], face.landmarks_5pt[1], face.landmarks_5pt[2]
        interocular = float(np.linalg.norm(right_eye - left_eye))
        eye_mid_x = float((left_eye[0] + right_eye[0]) / 2.0)
        nose_offset_ratio = float((nose[0] - eye_mid_x) / max(interocular, 1e-4))
        self._nose_offsets.append(nose_offset_ratio)
        self._yaws.append(face.yaw)

        depth_pose_reading = LandmarkGeometry.check_depth_pose(self._nose_offsets, self._yaws)
        self._update_cue(LivenessCue.DEPTH_POSE, depth_pose_reading)

        # 3D vs Flat homography
        self._landmarks_history.append(face.landmarks_5pt)
        flat_vs_3d_reading = LandmarkGeometry.check_flat_vs_3d(self._landmarks_history)
        self._update_cue(LivenessCue.FLAT_VS_3D, flat_vs_3d_reading)

        # Evaluate decision
        return self._evaluate()

    def _update_cue(self, cue: LivenessCue, reading: CueReading) -> None:
        state = self.cue_states[cue]
        state.reading = reading

        level_thresh = self.tuning.level_for(cue)
        frames_thresh = self.tuning.frames_for(cue)

        if reading.confidence > 0.4 and reading.level >= level_thresh:
            state.frames_counted += 1
            if state.frames_counted >= frames_thresh:
                state.has_fired = True

    def _evaluate(self) -> LivenessDecision:
        # Check DENY cues first (active rejection overrides any confirmation)
        for cue in [LivenessCue.GLOSS_GLARE, LivenessCue.DEVICE_DETECTED]:
            if self.cue_states[cue].has_fired:
                reason = "Screen glare detected (looks like a photo on a display)." if cue == LivenessCue.GLOSS_GLARE else "Device bezel detected framing the face."
                return LivenessDecision(
                    kind=LivenessDecisionKind.DENIED,
                    cue=cue,
                    reason=reason
                )

        # Check CONFIRM cues
        fired_confirm = None
        for cue in [LivenessCue.BLINK, LivenessCue.DEPTH_POSE, LivenessCue.FLAT_VS_3D]:
            if self.cue_states[cue].has_fired:
                fired_confirm = cue
                break

        if self.mode == "light":
            # In light mode: confirmed after minimum frames if no deny cues fired
            if fired_confirm is not None:
                return LivenessDecision(kind=LivenessDecisionKind.CONFIRMED, cue=fired_confirm)
            if self.total_frames_observed >= self.tuning.light_mode_min_frames:
                return LivenessDecision(kind=LivenessDecisionKind.CONFIRMED, cue=None)
        elif self.mode == "heavy":
            # In heavy mode: requires at least one confirm cue to pass
            if fired_confirm is not None:
                return LivenessDecision(kind=LivenessDecisionKind.CONFIRMED, cue=fired_confirm)

        return LivenessDecision(kind=LivenessDecisionKind.PENDING)
