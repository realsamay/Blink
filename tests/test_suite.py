"""
End-to-end self-test suite for Blink Linux face unlock implementation.
Validates crypto, vision pipeline, liveness decision tree, and system keystrokes.
"""

from __future__ import annotations

import os
import sys
import unittest
import numpy as np

from blink.core.config import BlinkSettings
from blink.core.crypto import SecureCredentialManager, SecureFaceStore
from blink.vision.detector import FaceDetector, DetectedFace
from blink.vision.aligner import FaceAligner
from blink.vision.embedder import ArcFaceEmbedder
from blink.liveness.cues import (
    LivenessCue,
    LivenessDecisionKind,
    CueReading,
)
from blink.liveness.analyzer import LivenessAnalyzer
from blink.liveness.extractors import GlareDetector, DeviceBezelDetector, LandmarkGeometry
from blink.system.keystroke_injector import KeystrokeInjector, CHAR_MAP


class TestBlinkCrypto(unittest.TestCase):
    def setUp(self):
        self._orig_identities = SecureFaceStore.load_identities()
        self._orig_pwd = SecureCredentialManager.read_password()

    def tearDown(self):
        SecureFaceStore.save_identities(self._orig_identities)
        if self._orig_pwd:
            SecureCredentialManager.save_password(self._orig_pwd)
        else:
            SecureCredentialManager.remove_password()

    def test_credential_encryption_decryption(self):
        test_pwd = "SecretPassword123!@#"
        SecureCredentialManager.save_password(test_pwd)
        self.assertTrue(SecureCredentialManager.has_stored_password())

        decrypted = SecureCredentialManager.read_password()
        self.assertEqual(decrypted, test_pwd)

        # Remove password
        SecureCredentialManager.remove_password()
        self.assertFalse(SecureCredentialManager.has_stored_password())

    def test_face_store_encryption(self):
        sample_identity = [{
            "id": "test-uuid-1",
            "name": "Tester",
            "samples": [{"embedding": [0.1] * 512, "pose": "center"}],
            "is_enabled": True
        }]
        SecureFaceStore.save_identities(sample_identity)
        loaded = SecureFaceStore.load_identities()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "Tester")
        self.assertEqual(len(loaded[0]["samples"][0]["embedding"]), 512)


class TestBlinkVision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = FaceDetector()
        cls.aligner = FaceAligner()
        cls.embedder = ArcFaceEmbedder()

    def test_detector_initialization(self):
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        faces = self.detector.detect_faces(dummy)
        self.assertIsInstance(faces, list)

    def test_aligner_similarity_transform(self):
        dummy_face = DetectedFace(
            bbox=(100, 100, 120, 140),
            score=0.99,
            landmarks_5pt=np.array([
                [120, 130],  # left eye
                [180, 130],  # right eye
                [150, 160],  # nose
                [130, 200],  # left mouth
                [170, 200],  # right mouth
            ], dtype=np.float32)
        )
        dummy_img = np.full((300, 300, 3), 128, dtype=np.uint8)
        warped = self.aligner.align(dummy_face, dummy_img)
        self.assertIsNotNone(warped)
        self.assertEqual(warped.shape, (112, 112, 3))

    def test_arcface_embedder(self):
        dummy_aligned = np.random.randint(0, 255, (112, 112, 3), dtype=np.uint8)
        emb = self.embedder.compute_embedding(dummy_aligned)
        self.assertEqual(emb.shape, (512,))
        norm = np.linalg.norm(emb)
        self.assertAlmostEqual(norm, 1.0, places=4)

        # Self-similarity should be 1.0
        sim = self.embedder.cosine_similarity(emb, emb)
        self.assertAlmostEqual(sim, 1.0, places=4)


class TestBlinkLiveness(unittest.TestCase):
    def test_glare_detection_on_white_specular(self):
        # Image with direct white specular glare blob
        face_crop = np.full((100, 100, 3), 80, dtype=np.uint8)
        # Add high luma near-white specular blob
        face_crop[40:60, 40:60] = 255
        reading = GlareDetector.extract(face_crop)
        self.assertGreater(reading.level, 0.05)

    def test_liveness_decision_model(self):
        analyzer = LivenessAnalyzer(mode="light", window_seconds=2.0)
        analyzer.reset()

        dummy_face = DetectedFace(
            bbox=(100, 100, 150, 150),
            score=0.95,
            landmarks_5pt=np.array([[120, 130], [180, 130], [150, 160], [130, 200], [170, 200]]),
            yaw=0.0
        )
        dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)

        # Feed 3 normal frames in light mode -> should auto-confirm
        d1 = analyzer.observe(dummy_frame, dummy_face)
        d2 = analyzer.observe(dummy_frame, dummy_face)
        d3 = analyzer.observe(dummy_frame, dummy_face)
        self.assertEqual(d3.kind, LivenessDecisionKind.CONFIRMED)


class TestKeystrokeInjector(unittest.TestCase):
    def test_uinput_availability(self):
        available = KeystrokeInjector.is_available()
        self.assertTrue(available, "/dev/uinput should be writable")

    def test_charmap_coverage(self):
        # Ensure all standard alphanumeric keys and symbols are in CHAR_MAP
        test_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()-_=+[]{}|;:',.<>/?"
        for c in test_chars:
            self.assertIn(c, CHAR_MAP, f"Character '{c}' missing from CHAR_MAP")


class TestBiometricMatching(unittest.TestCase):
    def test_composite_scoring_and_rejection(self):
        identities = SecureFaceStore.load_identities()
        if not identities:
            return
        user = identities[0]
        samples = user.get("samples", [])
        if not samples:
            return

        # 1. Enrolled poses must match user with threshold=0.60
        for sample in samples:
            emb = np.array(sample["embedding"], dtype=np.float32)
            scored = ArcFaceEmbedder.score_identities(emb, identities)
            matched = ArcFaceEmbedder.best_match(scored, threshold=0.60)
            self.assertIsNotNone(matched, f"Pose {sample['pose']} must match user")
            self.assertEqual(matched[0]["name"], user["name"])

        # 2. Impostor/random embedding must be rejected
        impostor_emb = np.random.randn(512).astype(np.float32)
        impostor_emb /= np.linalg.norm(impostor_emb)
        scored = ArcFaceEmbedder.score_identities(impostor_emb, identities)
        matched = ArcFaceEmbedder.best_match(scored, threshold=0.60)
        self.assertIsNone(matched, "Random impostor must be rejected")


if __name__ == "__main__":
    unittest.main()
