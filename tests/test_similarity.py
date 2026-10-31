import numpy as np

def test_cosine_similarity():
    a = np.array([1.0, 0.0])
    b = np.array([1.0, 0.0])
    dot = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    assert np.isclose(dot, 1.0)
