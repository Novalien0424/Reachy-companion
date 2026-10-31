"""`camera` and `look_around` say what the TV plays, next to the picture (2026-10-31, follow-up).

Asked 「瑞奇，我們在哪裡？」 during a clip, mini called `camera` 2 of 3 times
even with the trip's place in the nas result. The picture shows the room; the
video's title and place now travel with it, by name, while a video Reachy
started is on the TV.
"""

from __future__ import annotations
import base64
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from reachy_companion.hanova import tv_session
from reachy_companion.tools.camera import Camera
from reachy_companion.tools.move_head import MoveHead
from reachy_companion.tools.look_around import LookAround


@pytest.fixture(autouse=True)
def _clean() -> Any:
    tv_session.reset_for_tests()
    yield
    tv_session.reset_for_tests()


def _deps() -> SimpleNamespace:
    media = SimpleNamespace(get_frame_jpeg=lambda: b"\xff\xd8jpeg")
    reachy_mini = SimpleNamespace(
        media=media,
        get_current_head_pose=MagicMock(return_value=object()),
        get_current_joint_positions=MagicMock(return_value=([0.0] * 6, [0.0, 0.0])),
    )
    return SimpleNamespace(
        reachy_mini=reachy_mini, movement_manager=MagicMock(), camera_enabled=True, motion_duration_s=0.01
    )


@pytest.mark.asyncio
async def test_the_camera_names_the_video_on_the_tv_only_while_one_plays():
    """No video: the picture alone. A video: its title and place beside it, until it ends."""
    assert "video_on_tv" not in await Camera()(_deps(), question="我們在哪裡")

    tv_session.mark_started("nas", title="2019 Coast morning", place="Coast", recast="http://nas.example.invalid/x")
    out = await Camera()(_deps(), question="我們在哪裡")
    assert out["b64_im"] and out["video_on_tv"] == {"title": "2019 Coast morning", "place": "Coast"}

    tv_session.mark_ended("stop")
    assert "video_on_tv" not in await Camera()(_deps(), question="我們在哪裡")


@pytest.mark.asyncio
async def test_a_youtube_video_has_a_title_and_no_place():
    """`play_video` passes the title only; an unknown place is left out, not sent as null."""
    tv_session.mark_started("youtube", title="An MV", recast="vid123")
    out = await Camera()(_deps(), question="這是什麼")
    assert out["video_on_tv"] == {"title": "An MV"}


@pytest.mark.asyncio
async def test_look_around_names_the_video_on_the_tv_only_while_one_plays(monkeypatch: pytest.MonkeyPatch):
    """The same field, next to the picture taken after the turn."""

    async def _queue(self: MoveHead, deps: Any, direction: str) -> dict[str, Any]:
        return {"status": "move_queued", "direction_requested": direction}

    async def _camera(self: Camera, deps: Any, **kwargs: Any) -> dict[str, Any]:
        return {"b64_im": base64.b64encode(b"jpeg").decode("utf-8")}

    monkeypatch.setattr(MoveHead, "queue_direction", _queue)
    monkeypatch.setattr(Camera, "__call__", _camera)
    monkeypatch.setattr("reachy_companion.tools.look_around.LOOK_AROUND_SETTLE_S", 0.0)

    assert "video_on_tv" not in await LookAround()(_deps(), direction="right")
    tv_session.mark_started("nas", title="2019 Coast morning", place="Coast")
    out = await LookAround()(_deps(), direction="right")
    assert out["b64_im"] and out["video_on_tv"] == {"title": "2019 Coast morning", "place": "Coast"}
