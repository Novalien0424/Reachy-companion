import numpy as np

from reachy_companion.audio.resample import resample_pcm


def test_upsample_16k_to_24k_length_and_dtype():
    """Upsampling 16 kHz to 24 kHz should scale length by 3/2 and stay float32."""
    frame = np.sin(np.linspace(0, 2 * np.pi * 220, 1600)).astype(np.float32)  # 100 ms @ 16 kHz
    out = resample_pcm(frame, 16000, 24000)
    assert out.dtype == np.float32
    assert abs(len(out) - 2400) <= 2


def test_downsample_24k_to_16k_roundtrip_energy():
    """Downsampling 24 kHz to 16 kHz should scale length by 2/3 and preserve energy."""
    frame = np.sin(np.linspace(0, 2 * np.pi * 220, 2400)).astype(np.float32)
    out = resample_pcm(frame, 24000, 16000)
    assert abs(len(out) - 1600) <= 2
    assert 0.8 < (np.abs(out).mean() / np.abs(frame).mean()) < 1.2


def test_same_rate_is_identity():
    """Matching rates should short-circuit and return the very same array object."""
    frame = np.zeros(160, dtype=np.float32)
    assert resample_pcm(frame, 16000, 16000) is frame


def test_2d_channel_first_frames_resample_on_sample_axis():
    """Channel-first (1, N) frames should resample the sample axis, not the channel axis."""
    # Model PCM arrives shaped (1, N) (huggingface_realtime.py:843) —
    # the SAMPLE axis is the last one; a wrong-axis resample leaves N unchanged.
    frame = np.zeros((1, 2400), dtype=np.float32)
    out = resample_pcm(frame, 24000, 16000)
    assert out.shape[0] == 1
    assert abs(out.shape[-1] - 1600) <= 2
