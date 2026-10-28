# Local voice detection for the listening reaction — plan (2026-10-28)

Port of the listening half of upstream `8b37f31` (#569, "Add local Silero
VAD for faster physical listening"): `audio/local_vad.py` (`SileroVAD`,
checksum-pinned ONNX model, single CPU thread) drives the antennas' listening
pose from the robot's own microphone, so the body reacts ~160 ms after speech
starts instead of waiting for the server's `speech_started` round trip.

Not ported (ours supersedes): local barge-in ducking, paced playback,
response-id discard — D-023/D-028/D-032 own interruption.

## Design
- `audio/local_vad.py` from upstream (model fetch + sha256 check, cached via
  `huggingface_hub.cached_assets_path`, `onnxruntime` single thread). No new
  dependency: onnxruntime 1.27.0 is already in the robot's apps venv and our
  lock (transitive); import is guarded — missing or failing → one warning,
  feature off, conversation unaffected.
- Inference runs in the microphone reader thread (A3), never on the event
  loop: each frame → mono 16 kHz → VAD chunks; state transitions are handed to
  the loop.
- Local speech start → `MovementManager.set_listening(True)`; local speech end
  (upstream's 400 ms silence) → `set_listening(False)` — only while the robot
  is not speaking. While a reply is playing, the existing server-event path
  keeps sole control (our barge machine and the robot's own voice must not
  move the antennas). Server-event listening calls stay as they are; the two
  sources are idempotent on the same flag (B2 removed the debounce).
- Mic muted / sleep quiesce / warmup → VAD fed nothing.
- `LOCAL_VAD_LISTENING` env (default on); `scripts/preload_assets.py` fetches
  the model so the first boot after a deploy does not download it.
- Log the first local speech start per session at INFO with the lead time
  over the server's `speech_started` when both arrive (evidence for the gain).

## Verify
Port upstream's `local_vad` tests; add: VAD runs off-loop; listening toggles
on local start/end when idle; no toggles while a reply plays; muted/sleep
feed nothing; import failure disables cleanly; env off disables. Gate: suite,
ruff, mypy. On robot: the lead-time log line; antennas react on the first
syllable; no antenna twitching while the robot itself talks.

## Self-review log
1. *Echo of the robot's own voice?* Gated: local VAD does not drive listening
   while a reply plays. Accepted.
2. *Conflict with server-driven listening?* Both set the same idempotent flag;
   server events still win while speaking. Accepted.
3. *CPU on the CM4?* Single-thread ONNX on 32 ms chunks, off the loop;
   upstream ships it on the same hardware. Accepted, measure on robot.
4. *Network at boot?* Model preloaded at deploy; fetch failure → feature off.
