"""
Compatibility shim for liveness analyzer and cues.
Re-exports from blink.liveness to maintain backward compatibility.
"""

from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.cues import LivenessDecisionKind

__all__ = ["LivenessAnalyzer", "LivenessDecisionKind"]
