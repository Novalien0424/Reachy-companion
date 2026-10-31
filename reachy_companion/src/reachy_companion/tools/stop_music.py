"""Stop, pause and resume what Reachy plays, on the speaker and the TV (D-018, R2). Filename == Tool.name.

2026-10-31: 「停下你的播放」 reached this tool while a NAS clip played on the TV,
got `nothing_playing`, and Reachy said it had stopped while the video went on.
Then the operator's rule, "stop stops everything" (plan W1): each action works
on the speaker **and** the TV in the same call, including a video Reachy did not
start, and the result names each half it changed (`stopped: ["speaker", "tv"]`).
Each TV half is read back (`ha_client.stop_tv_playback` / `pause_tv_playback` /
`resume_tv_playback`), so a receiver that ignored the command is reported,
never assumed.

`pause` and `resume` (plan W2) live here beside `stop` as `PausePlayback` and
`ResumePlayback`.

**Stop keeps the place** (2026-10-31, follow-up). 「暫停」 with a clip playing
went to `music stop` 4 of 6 times after two wording revisions, so the shape
changed instead: stopping a video Reachy started saves a bookmark
(`tv_session.save_bookmark`) from the TV's last position, and a `resume` that
finds nothing paused re-casts it and seeks there (`recast_from_bookmark`). Stop
still really stops; only the speaker half is unchanged. They are actions of the `music` family only; this module is
not in the profile's `default_tools`, so none of its classes registers on its
own (`tool_family.py`).

This tool must always answer. Upstream's single-threaded server could not stop
music while a download was in flight; here every tool is its own asyncio task
(`huggingface_realtime.py:1011`), so this one is never starved.

It is also the one ported tool with **no prerequisites at all**
(`settings.TOOL_PREREQS["stop_music"] == ()`, review finding 10): a robot that
cannot be silenced by voice is a safety defect, so this tool stays reachable even
when yt-dlp is missing and nothing could have started the music in the first
place. That is a deliberate exemption, not a missing family check. Pause and
resume share it: they check nothing before acting either.

Logging here is status codes only; titles never reach a log line.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, List, Awaitable

from reachy_companion.hanova import nas, settings, tv_session
from reachy_companion.hanova.ha_client import (
    stop_tv_playback,
    is_playback_state,
    pause_tv_playback,
    resume_tv_playback,
    recast_from_bookmark,
)
from reachy_companion.tools.core_tools import Tool, ToolDependencies
from reachy_companion.hanova.music_player import PLAYER


logger = logging.getLogger(__name__)

_EMPTY: Dict[str, Any] = {"type": "object", "properties": {}, "required": []}
# Said wherever a resume finds nothing to resume: live 2026-10-31, asked to 暫停,
# Reachy said a stopped video was waiting to go on.
_STOPPED_IS_NOT_PAUSED = (
    "Something that was stopped is not paused: a stopped video is played again with nas or youtube, and a "
    "stopped song with music play, from the beginning."
)


def _tv_entity() -> str:
    """Return the cast target, or "" when Home Assistant is not set up to reach it."""
    if not (settings.ha_url() and settings.ha_token()):
        return ""
    return settings.cast_entity()


async def _no_tv() -> None:
    """Stand in for the TV half when Home Assistant is not set up."""
    return None


async def _both(
    speaker: Awaitable[Dict[str, Any]], tv: Awaitable[Dict[str, Any]] | None
) -> tuple[Dict[str, Any], Dict[str, Any] | None]:
    """Run the speaker and the TV halves at once; the TV half is None without Home Assistant."""
    speaker_result, tv_result = await asyncio.gather(speaker, tv if tv is not None else _no_tv())
    return speaker_result, tv_result


def _end_session_if_gone(tv: Dict[str, Any] | None, reason: str | None = None) -> None:
    """Forget the video session when the TV read back as no longer in playback (plan W3)."""
    state = tv.get("state") if tv else None
    if state is not None and not is_playback_state(str(state)):
        tv_session.mark_ended(reason or f"the TV reads {state}")


def _titles(speaker: Dict[str, Any], tv: Dict[str, Any] | None, halves: List[str]) -> Dict[str, Any]:
    """Return `speaker_title` / `tv_title` for the halves named, when they are known."""
    titles: Dict[str, Any] = {}
    if "speaker" in halves and speaker.get("title"):
        titles["speaker_title"] = speaker["title"]
    if "tv" in halves and tv is not None and tv.get("title"):
        titles["tv_title"] = tv["title"]
    return titles


def _failure(done_key: str, done: List[str], sentences: List[str], titles: Dict[str, Any]) -> Dict[str, Any]:
    """Shape an error that still says which half did what was asked."""
    result: Dict[str, Any] = {"ok": False, "error": " ".join(sentences)}
    if done:
        result[done_key] = done
        result.update(titles)
    return result


_SPEAKER_DONE = {
    "stopped": "The music on your speaker stopped.",
    "paused": "The music on your speaker is paused.",
    "resumed": "The music on your speaker is playing again.",
}
_TV_DONE = {
    "stopped": "The video on the TV stopped.",
    "paused": "The video on the TV is paused.",
    "resumed": "The video on the TV is playing again.",
}


def _done_sentences(verb: str, done: List[str]) -> List[str]:
    sentences = []
    if "speaker" in done:
        sentences.append(_SPEAKER_DONE[verb])
    if "tv" in done:
        sentences.append(_TV_DONE[verb])
    return sentences


_PLACE_KEPT = "Its place is kept: if they say 「繼續」, music resume plays it on from there."


def _keep_place(tv: Dict[str, Any], playing: Dict[str, Any] | None) -> bool:
    """Save a bookmark for a stopped video Reachy started; return whether one was saved.

    Only Reachy's own casts, because only those can be cast again. A NAS clip
    is re-cast by the URL Reachy gave the TV, falling back to the TV's
    `media_content_id`; a YouTube video by the id the TV reports now (it may
    have moved on to the next video), falling back to the one Reachy cast.
    """
    if playing is None or playing.get("kind") not in ("nas", "youtube") or tv.get("position_s") is None:
        return False
    if playing["kind"] == "nas":
        recast = playing.get("recast") or tv.get("content_id")
    else:
        recast = tv.get("content_id") or playing.get("recast")
    if not recast:
        return False
    tv_session.save_bookmark(
        kind=str(playing["kind"]),
        recast=str(recast),
        title=tv.get("title") or playing.get("title"),
        place=playing.get("place"),
        position_s=float(tv["position_s"]),
    )
    return True


async def _resume_tv(entity: str) -> Dict[str, Any]:
    """Resume a paused TV video; with nothing paused, play a kept place on from where it stopped."""
    tv = await resume_tv_playback(entity)
    if tv["result"] != "not_paused":
        return tv
    mark = tv_session.bookmark()
    if mark is None:
        return tv
    again = await recast_from_bookmark(entity, mark)
    if again["result"] == "resumed":
        # On the TV again; this also clears the bookmark.
        tv_session.mark_started(str(mark["kind"]), title=mark.get("title"), place=mark.get("place"),
                                recast=str(mark["recast"]))
    return again


def _unreachable(verb: str) -> str:
    return (
        f"Home Assistant did not answer about the TV, so a video there could not be {verb}. "
        f"Tell the person the remote can do it; do not say the video was {verb}."
    )


class StopMusic(Tool):
    """Stop everything Reachy can stop: the speaker music and the video on the TV."""

    name = "stop_music"
    description = "Stop what is playing: the music on Reachy's speaker and the video on the TV."
    parameters_schema: Dict[str, Any] = _EMPTY

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Stop the speaker and the TV together, and read the TV back."""
        logger.info("Tool call: stop_music")
        entity = _tv_entity()
        playing = tv_session.now_playing()
        speaker, tv = await _both(PLAYER.stop(deps), stop_tv_playback(entity) if entity else None)
        logger.info("stop_music: speaker %s, TV %s", speaker.get("status"), tv["result"] if tv else "not set up")

        kept = tv is not None and tv["result"] == "stopped" and _keep_place(tv, playing)
        if tv is not None and tv["result"] in ("stopped", "not_playing"):
            # The trip is over: `skip` must not quietly carry it on, and the
            # answer-only-when-called rule for a video on screen ends with it.
            nas.clear_session()
            tv_session.mark_ended("stop")

        stopped = [half for half, did in (("speaker", speaker.get("status") == "stopped"),
                                          ("tv", tv is not None and tv["result"] == "stopped")) if did]
        titles = _titles(speaker, tv, stopped)
        problems: List[str] = []
        if speaker.get("status") == "stop_failed":
            problems.append(
                "The music on your speaker may still be playing: the speaker did not acknowledge the stop. "
                "Call stop again yourself once; if that fails too, tell the person."
            )
        if tv is not None and tv["result"] == "still_playing":
            problems.append(
                f"The video on the TV did not stop (it still reads {tv['state']}). Tell the person it is still "
                "playing and that the remote can stop it; do not say it stopped."
            )
        if tv is not None and tv["result"] == "unreachable":
            if speaker.get("status") == "nothing_playing":
                problems.append("Nothing was playing on your speaker.")
            problems.append(_unreachable("stopped"))
        if kept:
            titles["note"] = _PLACE_KEPT
        if problems:
            return _failure("stopped", stopped, _done_sentences("stopped", stopped) + problems, titles)
        if stopped:
            return {"ok": True, "status": "stopped", "stopped": stopped, **titles}
        where = "on your speaker or on the TV" if tv is not None else "on your speaker"
        return {
            "ok": True,
            "status": "nothing_playing",
            "detail": f"nothing is playing {where}, so nothing was stopped; say so, and do not say you "
            "stopped something",
        }


class PausePlayback(Tool):
    """Pause the speaker music and the video on the TV, so both can be resumed."""

    name = "pause_playback"
    description = "Pause what is playing: the music on Reachy's speaker and the video on the TV."
    parameters_schema: Dict[str, Any] = _EMPTY

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Pause the speaker and the TV together, and read the TV back."""
        logger.info("Tool call: pause_playback")
        entity = _tv_entity()
        speaker, tv = await _both(PLAYER.pause_by_user(deps), pause_tv_playback(entity) if entity else None)
        logger.info("pause_playback: speaker %s, TV %s", speaker.get("status"), tv["result"] if tv else "not set up")
        # Paused still counts as on screen (plan W3); only a TV that left playback ends the session.
        _end_session_if_gone(tv)

        paused = [half for half, did in (("speaker", speaker.get("status") == "paused"),
                                         ("tv", tv is not None and tv["result"] in ("paused", "already_paused")))
                  if did]
        titles = _titles(speaker, tv, paused)
        problems: List[str] = []
        if speaker.get("status") == "pause_failed":
            problems.append(
                "The music on your speaker did not pause: the speaker did not take the pause. Tell the person "
                "it is still playing; do not say it paused."
            )
        if speaker.get("status") == "superseded":
            problems.append(
                "The music on your speaker was not paused: another music request took over at the same moment."
            )
        if tv is not None and tv["result"] == "still_playing":
            problems.append(
                f"The video on the TV did not pause (it still reads {tv['state']}). Tell the person it is still "
                "playing and that the remote can pause it; do not say it paused."
            )
        if tv is not None and tv["result"] == "unreachable":
            if speaker.get("status") == "nothing_playing":
                problems.append("Nothing was playing on your speaker.")
            problems.append(_unreachable("paused"))
        if problems:
            return _failure("paused", paused, _done_sentences("paused", paused) + problems, titles)
        if paused:
            return {"ok": True, "status": "paused", "paused": paused, **titles}
        where = "on your speaker or on the TV" if tv is not None else "on your speaker"
        return {
            "ok": True,
            "status": "nothing_playing",
            "detail": f"nothing is playing {where}, so nothing was paused; say so, and do not say you paused "
            "something",
        }


class ResumePlayback(Tool):
    """Resume the speaker music and the TV video that are paused."""

    name = "resume_playback"
    description = "Resume what is paused: the music on Reachy's speaker and the video on the TV."
    parameters_schema: Dict[str, Any] = _EMPTY

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        """Resume the speaker and the TV together, and read the TV back."""
        logger.info("Tool call: resume_playback")
        entity = _tv_entity()
        speaker, tv = await _both(PLAYER.resume_by_user(deps), _resume_tv(entity) if entity else None)
        logger.info("resume_playback: speaker %s, TV %s", speaker.get("status"), tv["result"] if tv else "not set up")
        _end_session_if_gone(tv)

        resumed = [half for half, did in (("speaker", speaker.get("status") == "resumed"),
                                          ("tv", tv is not None and tv["result"] == "resumed")) if did]
        playing = [half for half, did in (("speaker", speaker.get("status") == "already_playing"),
                                          ("tv", tv is not None and tv["result"] == "already_playing")) if did]
        titles = _titles(speaker, tv, resumed or playing)
        if "tv" in resumed and tv is not None and tv.get("from"):
            titles["from"] = tv["from"]
        problems: List[str] = []
        if speaker.get("status") in ("resume_failed", "superseded"):
            problems.append(
                "The music on your speaker could not be resumed and is stopped now. Tell the person, and offer "
                "to play the song again with music play."
            )
        if tv is not None and tv["result"] == "still_paused":
            problems.append(
                "The video on the TV did not resume (it still reads paused). Tell the person the remote can start "
                "it again; do not say it is playing."
            )
        if tv is not None and tv["result"] == "ended":
            problems.append(
                f"The video on the TV could not be resumed: the TV now reads {tv['state']}, so it has ended. "
                f"{_STOPPED_IS_NOT_PAUSED} Ask whether to play it again."
            )
        if tv is not None and tv["result"] == "recast_failed":
            problems.append(
                "The stopped video could not be started again: Home Assistant did not accept it. Tell the person; "
                "it can be played again with nas or youtube."
            )
        if tv is not None and tv["result"] == "not_started":
            problems.append(
                "The stopped video was sent to the TV again, but the TV shows no playback; it may be off. Ask them "
                "to check that the TV is on, then try again; do not say it is playing."
            )
        if tv is not None and tv["result"] == "unreachable":
            problems.append(_unreachable("resumed"))
        if problems:
            return _failure("resumed", resumed, _done_sentences("resumed", resumed) + problems, titles)
        if resumed:
            return {"ok": True, "status": "resumed", "resumed": resumed, **titles}
        if playing:
            return {
                "ok": True,
                "status": "already_playing",
                "playing": playing,
                **titles,
                "detail": "nothing was paused: what is listed in playing is already playing; say so, and do "
                "not say you resumed it",
            }
        where = "on your speaker or on the TV" if tv is not None else "on your speaker"
        return {
            "ok": False,
            "status": "nothing_paused",
            "error": f"Nothing is paused {where}, so nothing was resumed. {_STOPPED_IS_NOT_PAUSED} Say so, and "
            "do not say you resumed something.",
        }
