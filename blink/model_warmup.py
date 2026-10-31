# Model pre-warming routine
import numpy as np

def warm_up_session(session):
    dummy = np.zeros((1, 3, 112, 112), dtype=np.float32)
    return session.run(None, {'input': dummy})
