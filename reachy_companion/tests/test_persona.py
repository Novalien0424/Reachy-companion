"""Tests for the operator-editable persona override (D-016)."""

import logging
from pathlib import Path
from collections.abc import Iterator

import pytest

import reachy_companion.persona as persona_mod
import reachy_companion.prompts as prompts_mod
from reachy_companion.config import config
from reachy_companion.profile_store import (
    DEFAULT_PROFILE_NAME,
    ProfileDefinition,
    read_profile,
    canonical_profile_name,
    read_profile_from_directory,
    read_packaged_default_profile,
)
from reachy_companion.profile_toolsets import read_profile_default_tool_names


PERSONA_BODY = "You are a stoic lighthouse keeper. Answer in one short sentence."


def builtin_profile() -> ProfileDefinition:
    """Read the active built-in profile document, bypassing any persona override."""
    profile_name = canonical_profile_name(config.REACHY_MINI_CUSTOM_PROFILE)
    if profile_name == DEFAULT_PROFILE_NAME:
        return read_packaged_default_profile()
    return read_profile_from_directory(profile_name, config.resolve_profile_dir(profile_name))


@pytest.fixture(autouse=True)
def isolated_persona_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Point the persona lookup at an empty instance directory and clear cached state."""
    monkeypatch.delenv(persona_mod.PERSONA_FILE_ENV, raising=False)
    monkeypatch.setattr(config, "INSTANCE_PATH", tmp_path)
    persona_mod.reset_persona_cache()
    yield tmp_path
    persona_mod.reset_persona_cache()


def write_persona(instance_dir: Path, text: str) -> Path:
    """Write a persona.md into an instance directory and return its path."""
    persona_path = instance_dir / persona_mod.PERSONA_FILENAME
    persona_path.write_text(text, encoding="utf-8")
    return persona_path


def active_profile() -> ProfileDefinition:
    """Read the active profile the way the running app does."""
    return read_profile(config.REACHY_MINI_CUSTOM_PROFILE)


def test_instance_persona_replaces_the_builtin_persona_text(tmp_path: Path) -> None:
    """A body-only persona.md supplies the system prompt used by the realtime session."""
    write_persona(tmp_path, PERSONA_BODY)

    assert prompts_mod.get_session_instructions(instance_path=tmp_path) == PERSONA_BODY
    assert builtin_profile().instructions != PERSONA_BODY


def test_persona_without_front_matter_keeps_every_builtin_metadata_field(tmp_path: Path) -> None:
    """Absent front matter means voice, greeting and default_tools stay built-in."""
    write_persona(tmp_path, PERSONA_BODY)

    builtin = builtin_profile()
    active = active_profile()

    assert active.instructions == PERSONA_BODY
    assert active.voice == builtin.voice
    assert active.greeting == builtin.greeting
    assert active.default_tools == builtin.default_tools


def test_persona_can_override_the_voice_alone(tmp_path: Path) -> None:
    """A persona.md voice wins while greeting and tools still come from the built-in profile."""
    write_persona(tmp_path, f'+++\nvoice = "verse"\n+++\n\n{PERSONA_BODY}\n')

    builtin = builtin_profile()

    assert prompts_mod.get_session_voice() == "verse"
    assert builtin.voice != "verse"
    assert active_profile().greeting == builtin.greeting
    assert active_profile().default_tools == builtin.default_tools


def test_persona_can_override_the_greeting_alone(tmp_path: Path) -> None:
    """A persona.md greeting wins while voice and tools still come from the built-in profile."""
    write_persona(tmp_path, f'+++\ngreeting = "Greet the user like a lighthouse foghorn."\n+++\n\n{PERSONA_BODY}\n')

    builtin = builtin_profile()

    assert prompts_mod.get_session_greeting_prompt() == "Greet the user like a lighthouse foghorn."
    assert active_profile().voice == builtin.voice
    assert active_profile().default_tools == builtin.default_tools


def test_persona_can_override_the_default_tools_alone(tmp_path: Path) -> None:
    """A persona.md default_tools list replaces the built-in tool selection."""
    write_persona(tmp_path, f'+++\ndefault_tools = ["camera", "play_emotion"]\n+++\n\n{PERSONA_BODY}\n')

    builtin = builtin_profile()

    assert read_profile_default_tool_names(config.REACHY_MINI_CUSTOM_PROFILE) == ["camera", "play_emotion"]
    assert builtin.default_tools != ("camera", "play_emotion")
    assert active_profile().voice == builtin.voice
    assert active_profile().greeting == builtin.greeting


def test_persona_accepts_a_verbatim_profile_document(tmp_path: Path) -> None:
    """Copying the shipped profile.md into persona.md and editing it works unchanged."""
    write_persona(
        tmp_path,
        f'+++\nschema_version = 1\ndefault_tools = ["camera"]\nvoice = "sage"\ngreeting = "Say hi."\n+++\n\n{PERSONA_BODY}\n',
    )

    active = active_profile()

    assert active.instructions == PERSONA_BODY
    assert active.voice == "sage"
    assert active.greeting == "Say hi."
    assert active.default_tools == ("camera",)


def test_missing_persona_file_keeps_the_builtin_profile(tmp_path: Path) -> None:
    """With no persona.md the app behaves exactly as it did before D-016."""
    assert not (tmp_path / persona_mod.PERSONA_FILENAME).exists()

    builtin = builtin_profile()
    active = active_profile()

    assert active == builtin
    assert prompts_mod.get_session_instructions(instance_path=tmp_path) == builtin.instructions


def test_malformed_persona_front_matter_warns_and_falls_back(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Broken TOML never half-applies: it warns and the built-in profile is used whole."""
    write_persona(tmp_path, f'+++\nvoice = "verse\n+++\n\n{PERSONA_BODY}\n')

    builtin = builtin_profile()
    with caplog.at_level(logging.WARNING, logger="reachy_companion.persona"):
        active = active_profile()

    assert active == builtin
    assert "persona" in caplog.text.lower()
    assert persona_mod.PERSONA_FILENAME in caplog.text


def test_unknown_persona_metadata_warns_and_falls_back(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A misspelled front-matter key is reported rather than silently ignored."""
    write_persona(tmp_path, f'+++\nvoic = "verse"\n+++\n\n{PERSONA_BODY}\n')

    with caplog.at_level(logging.WARNING, logger="reachy_companion.persona"):
        active = active_profile()

    assert active == builtin_profile()
    assert "voic" in caplog.text


def test_persona_with_an_empty_body_falls_back_entirely(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty persona body discards the whole file, front matter included."""
    write_persona(tmp_path, '+++\nvoice = "verse"\n+++\n\n   \n')

    builtin = builtin_profile()
    with caplog.at_level(logging.WARNING, logger="reachy_companion.persona"):
        active = active_profile()

    assert active == builtin
    assert prompts_mod.get_session_voice() == builtin.voice
    assert "empty" in caplog.text.lower()


def test_unreadable_persona_path_warns_and_falls_back(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A persona.md that is not a readable file degrades to the built-in profile."""
    (tmp_path / persona_mod.PERSONA_FILENAME).mkdir()

    with caplog.at_level(logging.WARNING, logger="reachy_companion.persona"):
        active = active_profile()

    assert active == builtin_profile()
    assert "not a file" in caplog.text


def test_persona_file_env_overrides_the_instance_location(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PERSONA_FILE points the loader at an arbitrary absolute path."""
    elsewhere = tmp_path / "elsewhere" / "custom_persona.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text(f'+++\nvoice = "ballad"\n+++\n\n{PERSONA_BODY}\n', encoding="utf-8")
    write_persona(tmp_path, "This instance file must be ignored.")
    monkeypatch.setenv(persona_mod.PERSONA_FILE_ENV, str(elsewhere))
    persona_mod.reset_persona_cache()

    active = active_profile()

    assert active.instructions == PERSONA_BODY
    assert active.voice == "ballad"


def test_persona_file_env_pointing_nowhere_warns_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An explicit PERSONA_FILE that does not exist is an operator error worth logging."""
    monkeypatch.setenv(persona_mod.PERSONA_FILE_ENV, str(tmp_path / "nope" / "persona.md"))
    persona_mod.reset_persona_cache()

    with caplog.at_level(logging.WARNING, logger="reachy_companion.persona"):
        active = active_profile()

    assert active == builtin_profile()
    assert persona_mod.PERSONA_FILE_ENV in caplog.text


def test_persona_override_does_not_leak_into_other_profiles(tmp_path: Path) -> None:
    """Only the active profile is overridden; reading another profile is untouched."""
    write_persona(tmp_path, PERSONA_BODY)

    other = read_profile("mad_scientist_assistant")

    assert other.instructions != PERSONA_BODY


def test_persona_source_log_names_the_instance_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Startup logs one INFO line naming persona.md as the source in use."""
    write_persona(tmp_path, PERSONA_BODY)

    with caplog.at_level(logging.INFO, logger="reachy_companion.persona"):
        source = persona_mod.log_persona_source(instance_path=tmp_path)

    assert source == persona_mod.INSTANCE_PERSONA_SOURCE
    assert "persona: instance persona.md" in caplog.text


def test_persona_source_log_names_the_builtin_profile(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Startup logs one INFO line naming the built-in profile when no override applies."""
    with caplog.at_level(logging.INFO, logger="reachy_companion.persona"):
        source = persona_mod.log_persona_source(instance_path=tmp_path)

    assert source == persona_mod.BUILTIN_PERSONA_SOURCE
    assert "persona: built-in locked profile" in caplog.text


def test_persona_source_log_reports_builtin_for_a_malformed_file(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A rejected persona.md must not be reported as the active source."""
    write_persona(tmp_path, "+++\nnot toml at all\n+++\n\nbody\n")

    with caplog.at_level(logging.INFO, logger="reachy_companion.persona"):
        source = persona_mod.log_persona_source(instance_path=tmp_path)

    assert source == persona_mod.BUILTIN_PERSONA_SOURCE
    assert "persona: built-in locked profile" in caplog.text
