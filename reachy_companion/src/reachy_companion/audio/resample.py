"""Rate conversion between robot audio (16 kHz) and gpt-realtime (24 kHz)."""
import numpy as np
from scipy.signal import resample_poly


def resample_pcm(frame: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample float32 PCM from src_rate to dst_rate, scaling length by dst/src."""
    if src_rate == dst_rate:
        return frame
    g = np.gcd(src_rate, dst_rate)
    # axis=-1: model PCM is (1, N) channel-first (huggingface_realtime.py:843);
    # 1-D mic frames are unaffected. Default axis=0 would resample the wrong dim.
    out: np.ndarray = resample_poly(frame.astype(np.float32), dst_rate // g, src_rate // g, axis=-1)
    return out.astype(np.float32)
