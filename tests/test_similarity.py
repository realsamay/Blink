import unittest
import numpy as np
from blink.core.config import BlinkSettings
from blink.vision.embedder import ArcFaceEmbedder


class TestBiometricMatching(unittest.TestCase):
    def setUp(self):
        self.embedder = ArcFaceEmbedder()

    def test_cosine_similarity_identical(self):
        v1 = np.random.randn(512).astype(np.float32)
        v1 /= np.linalg.norm(v1)
        sim = ArcFaceEmbedder.cosine_similarity(v1, v1)
        self.assertAlmostEqual(sim, 1.0, places=5)

    def test_stranger_rejection_at_low_similarity(self):
        """Simulate stranger embeddings (~0.40 - 0.52 similarity) and ensure rejection."""
        # Enrolled identity poses
        base = np.random.randn(512).astype(np.float32)
        base /= np.linalg.norm(base)

        enrolled_identity = {
            "id": "user-1",
            "name": "Legit User",
            "samples": [
                {"embedding": base.tolist(), "pose": "center"},
                {"embedding": (base + 0.05 * np.random.randn(512)).tolist(), "pose": "left"},
            ],
            "is_enabled": True
        }
        # Normalize the samples
        for s in enrolled_identity["samples"]:
            arr = np.array(s["embedding"], dtype=np.float32)
            s["embedding"] = (arr / np.linalg.norm(arr)).tolist()

        # Stranger with random embedding (~0.0 - 0.5 similarity)
        stranger_emb = np.random.randn(512).astype(np.float32)
        stranger_emb /= np.linalg.norm(stranger_emb)

        scored = self.embedder.score_identities(stranger_emb, [enrolled_identity])
        # Even if stranger scores ~0.40, best_match must reject it
        matched = self.embedder.best_match(scored, threshold=0.65)
        self.assertIsNone(matched, "Stranger must not be matched!")

        # Also test with a simulated marginal score of 0.50 (old flawed threshold)
        scored_marginal = [(enrolled_identity, 0.50, 0.52)]
        matched_marginal = self.embedder.best_match(scored_marginal, threshold=0.65)
        self.assertIsNone(matched_marginal, "0.50 score must be rejected under safety floor.")

    def test_legit_user_accepted(self):
        """Enrolled user with high similarity (>= 0.75) is accepted."""
        base = np.random.randn(512).astype(np.float32)
        base /= np.linalg.norm(base)

        enrolled_identity = {
            "id": "user-1",
            "name": "Samay",
            "samples": [
                {"embedding": base.tolist(), "pose": "center"},
            ],
            "is_enabled": True
        }

        # Query embedding very close to base (e.g. 0.85 similarity)
        noise = np.random.randn(512).astype(np.float32)
        noise /= np.linalg.norm(noise)
        query = 0.85 * base + 0.15 * noise
        query /= np.linalg.norm(query)

        scored = self.embedder.score_identities(query, [enrolled_identity])
        matched = self.embedder.best_match(scored, threshold=0.65)
        self.assertIsNotNone(matched)
        matched_ident, centroid_sim, max_sim = matched
        self.assertEqual(matched_ident["name"], "Samay")
        self.assertGreaterEqual(centroid_sim, 0.65)

    def test_settings_auto_heal_low_threshold(self):
        """Verify that unsafe threshold (< 0.55) is automatically healed to 0.65."""
        s = BlinkSettings(match_threshold=0.35)
        # When loaded from dict, load() auto-corrects
        from unittest.mock import patch, mock_open
        import json
        fake_json = json.dumps({"match_threshold": 0.40, "is_face_unlock_enabled": True})
        with patch("pathlib.Path.exists", return_value=True), patch("builtins.open", mock_open(read_data=fake_json)):
            loaded = BlinkSettings.load()
            self.assertGreaterEqual(loaded.match_threshold, 0.55)
            self.assertEqual(loaded.match_threshold, 0.65)

    def test_multi_face_unlock_and_promotion(self):
        """Verify that when multiple identities are enrolled, the active face is correctly matched."""
        # Alice (single pose)
        v_alice = np.random.randn(512).astype(np.float32)
        v_alice /= np.linalg.norm(v_alice)
        alice = {
            "id": "alice-1",
            "name": "Alice",
            "is_enabled": True,
            "samples": [{"embedding": v_alice.tolist()}]
        }

        # Bob (multiple poses across various angles)
        v_bob = np.random.randn(512).astype(np.float32)
        v_bob /= np.linalg.norm(v_bob)
        bob_samples = []
        for _ in range(6):
            noise = np.random.randn(512).astype(np.float32) * 0.25
            sample = v_bob + noise
            sample /= np.linalg.norm(sample)
            bob_samples.append({"embedding": sample.tolist()})

        bob = {
            "id": "bob-1",
            "name": "Bob",
            "is_enabled": True,
            "samples": bob_samples
        }

        # Query is Bob closely matching Bob's first sample (~0.88 similarity)
        target_sample = np.array(bob_samples[0]["embedding"], dtype=np.float32)
        q_noise = np.random.randn(512).astype(np.float32)
        q_noise /= np.linalg.norm(q_noise)
        query = 0.85 * target_sample + 0.15 * q_noise
        query /= np.linalg.norm(query)

        scored = self.embedder.score_identities(query, [alice, bob])
        # Bob must be ranked at index 0 due to composite similarity
        self.assertEqual(scored[0][0]["name"], "Bob", "Bob must be ranked highest when facing the camera")

        matched = self.embedder.best_match(scored, threshold=0.65)
        self.assertIsNotNone(matched, "Bob must successfully unlock in multi-face setup")
        self.assertEqual(matched[0]["name"], "Bob")

    def test_single_face_diverse_centroid_cohesion(self):
        """Enrolled user with slight angle/lighting variation unlocks smoothly without threshold struggle."""
        base = np.random.randn(512).astype(np.float32)
        base /= np.linalg.norm(base)

        user = {
            "id": "user-2",
            "name": "Developer",
            "is_enabled": True,
            "samples": [
                {"embedding": base.tolist()},
                {"embedding": (base + 0.3 * np.random.randn(512)).tolist()}
            ]
        }
        for s in user["samples"]:
            arr = np.array(s["embedding"], dtype=np.float32)
            s["embedding"] = (arr / np.linalg.norm(arr)).tolist()

        # Query matches sample 0 at ~0.78, centroid around 0.55
        noise = np.random.randn(512).astype(np.float32)
        noise /= np.linalg.norm(noise)
        query = 0.78 * base + 0.22 * noise
        query /= np.linalg.norm(query)

        scored = self.embedder.score_identities(query, [user])
        matched = self.embedder.best_match(scored, threshold=0.65)
        self.assertIsNotNone(matched, "Genuine face must unlock smoothly")
        self.assertEqual(matched[0]["name"], "Developer")


if __name__ == "__main__":
    unittest.main()
