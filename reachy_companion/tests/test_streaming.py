import asyncio

import numpy as np
import pytest

from reachy_companion.streaming import (
    AdditionalOutputs,
    wait_for_item,
    audio_to_int16,
    audio_to_float32,
)


def test_additional_outputs_stores_messages() -> None:
    """Additional outputs should keep the emitted role/content payloads."""
    message = {"role": "assistant", "content": "hello"}

    outputs = AdditionalOutputs(message)

    assert outputs.args == (message,)


@pytest.mark.asyncio
async def test_wait_for_item_returns_none_on_timeout() -> None:
    """Queue waits should time out without raising."""
    queue: asyncio.Queue[int] = asyncio.Queue()

    assert await wait_for_item(queue, timeout=0.01) is None


def test_audio_to_int16_scales_float32() -> None:
    """Float audio should scale into the int16 sample range."""
    audio = np.array([-1.0, 0.0, 1.0], dtype=np.float32)

    np.testing.assert_array_equal(audio_to_int16(audio), np.array([-32767, 0, 32767], dtype=np.int16))


def test_audio_to_int16_saturates_instead_of_wrapping() -> None:
    """Float above full scale must clamp to the rail, never fold to the opposite one (D-017).

    `(x * 32767).astype(np.int16)` on an out-of-range float is undefined C
    behaviour that wraps in practice: 1.001 lands near -32768, i.e. the loudest
    possible artefact — a full-scale polarity flip — from an input that was only
    0.1 % too loud. The clip is what makes this function safe for any caller,
    not just the already-int16 one it has today.
    """
    audio = np.array([-4.0, -1.001, -1.0, 0.0, 1.0, 1.001, 4.0], dtype=np.float32)

    got = audio_to_int16(audio)

    np.testing.assert_array_equal(got, np.array([-32767, -32767, -32767, 0, 32767, 32767, 32767], dtype=np.int16))
    # The specific failure being guarded: no positive input may come back negative.
    assert not bool(np.any(got[audio > 0.0] < 0))
    assert not bool(np.any(got[audio < 0.0] > 0))


def test_audio_to_float32_scales_int16() -> None:
    """Int16 audio should scale into the float sample range."""
    audio = np.array([-32768, 0, 32767], dtype=np.int16)

    np.testing.assert_allclose(audio_to_float32(audio), np.array([-1.0, 0.0, 32767 / 32768], dtype=np.float32))


def test_audio_converters_reject_unsupported_dtype() -> None:
    """Unsupported audio dtypes should fail explicitly."""
    audio = np.array([1.0], dtype=np.float64)

    with pytest.raises(TypeError, match="Unsupported audio data type"):
        audio_to_int16(audio)
