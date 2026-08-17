"""Warm the HF caches the demos rely on (emotion clips + YuNet face model)."""
from huggingface_hub import hf_hub_download
from reachy_mini.motion.recorded_move import RecordedMoves

# Face-detection model (daemon side). Mirror repo/file/revision pinned in
# reachy_mini/vision/face_detector.py.
hf_hub_download("pollen-robotics/face_detection_yunet_2026may",
                "face_detection_yunet_2026may.onnx")
print("cached: YuNet face model")

lib = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
for name in ("welcoming2", "grateful1", "loving1", "surprised1", "sad1"):
    lib.get(name)
    print("cached:", name)
print("done")
