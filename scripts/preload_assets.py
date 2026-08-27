"""Warm the HF caches the demos rely on (emotion clips + YuNet + SFace face models)."""
from huggingface_hub import hf_hub_download
from reachy_mini.motion.recorded_move import RecordedMoves

# Face-detection model (daemon side). Import the SDK's own pins so the warmed
# cache entry is exactly the revision FaceDetector loads — an unpinned download
# warms a *different* entry and the robot still hits the network at wake time.
from reachy_mini.vision.face_detector import _MODEL_FILE, _MODEL_REPO, _MODEL_REVISION

hf_hub_download(_MODEL_REPO, _MODEL_FILE, revision=_MODEL_REVISION)
print("cached: YuNet face model")

# Face-recognition model (app side, D-013). ~37 MB, Apache-2.0. Mirrors
# reachy_companion/face_id.py SFACE_REPO / SFACE_FILE. Without this the first
# wake-time recognition builds its session off a cold cache — a visible stall.
hf_hub_download("opencv/face_recognition_sface",
                "face_recognition_sface_2021dec.onnx")
print("cached: SFace face-recognition model")

lib = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
for name in ("welcoming2", "grateful1", "loving1", "surprised1", "sad1"):
    lib.get(name)
    print("cached:", name)
print("done")
