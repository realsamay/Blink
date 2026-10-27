"""
Liveness decision model definitions: cues, roles, modes, and decisions.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, Dict, Set


class LivenessCueRole(Enum):
    DENY = "deny"        # Evidence of spoof; overrides everything
    CONFIRM = "confirm"  # Evidence of real 3D human


class LivenessCue(Enum):
    GLOSS_GLARE = "gloss_glare"
    DEVICE_DETECTED = "device_detected"
    FLAT_VS_3D = "flat_vs_3d"
    DEPTH_POSE = "depth_pose"
    BLINK = "blink"

    @property
    def title(self) -> str:
        return {
            LivenessCue.GLOSS_GLARE: "Gloss/glare",
            LivenessCue.DEVICE_DETECTED: "Device detected",
            LivenessCue.FLAT_VS_3D: "Flat vs 3D",
            LivenessCue.DEPTH_POSE: "Depth/pose",
            LivenessCue.BLINK: "Blink",
        }[self]

    @property
    def role(self) -> LivenessCueRole:
        if self in (LivenessCue.GLOSS_GLARE, LivenessCue.DEVICE_DETECTED):
            return LivenessCueRole.DENY
        return LivenessCueRole.CONFIRM


@dataclass
class CueReading:
    level: float = 0.0       # 0.0 to 1.0
    confidence: float = 0.0  # 0.0 to 1.0


@dataclass
class LivenessTuning:
    gloss_level: float = 0.04
    gloss_frames: int = 3
    device_level: float = 0.15
    device_frames: int = 3
    flat_vs_3d_level: float = 0.25
    flat_vs_3d_frames: int = 2
    depth_pose_level: float = 0.8
    depth_pose_frames: int = 2
    blink_frames: int = 1
    light_mode_min_frames: int = 3

    def level_for(self, cue: LivenessCue) -> float:
        return {
            LivenessCue.GLOSS_GLARE: self.gloss_level,
            LivenessCue.DEVICE_DETECTED: self.device_level,
            LivenessCue.FLAT_VS_3D: self.flat_vs_3d_level,
            LivenessCue.DEPTH_POSE: self.depth_pose_level,
            LivenessCue.BLINK: 0.5,
        }[cue]

    def frames_for(self, cue: LivenessCue) -> int:
        return {
            LivenessCue.GLOSS_GLARE: self.gloss_frames,
            LivenessCue.DEVICE_DETECTED: self.device_frames,
            LivenessCue.FLAT_VS_3D: self.flat_vs_3d_frames,
            LivenessCue.DEPTH_POSE: self.depth_pose_frames,
            LivenessCue.BLINK: self.blink_frames,
        }[cue]


@dataclass
class CueState:
    reading: CueReading = field(default_factory=CueReading)
    frames_counted: int = 0
    has_fired: bool = False

    def progress(self, threshold: int) -> float:
        if threshold <= 0:
            return 1.0 if self.has_fired else 0.0
        return min(1.0, float(self.frames_counted) / float(threshold))


class LivenessDecisionKind(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DENIED = "denied"


@dataclass
class LivenessDecision:
    kind: LivenessDecisionKind
    cue: Optional[LivenessCue] = None
    reason: Optional[str] = None
