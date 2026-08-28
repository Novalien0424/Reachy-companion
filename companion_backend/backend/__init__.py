"""Mac-side management backend for the Reachy Mini companion.

This package owns the *full* person record — name, photos, facts, and the SFace
embeddings computed from those photos — on the operator's Mac. The robot only
ever receives a projection of it (`reachy_companion`'s `faces.v1.json` and
`people.v1.json`), so the Mac is the durable side and a robot reinstall is no
longer a data loss.

It is deliberately run as a plain directory rather than an installed
distribution: `run.sh` execs uvicorn from `companion_backend/` using the
existing `reachy_companion/.venv`, which is also where `reachy_companion`
itself is importable from — the store below reuses that package's
normalization rules directly so the two sides cannot drift.
"""
