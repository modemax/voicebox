# Loki Fork — modemax/voicebox

This is Loki's maintained fork of [jamiepine/voicebox](https://github.com/jamiepine/voicebox)
(KB-0358). Only the **backend** runs — headless, as a launchd daemon on Chris's Mac, serving
Loki's TTS through `POST /generate/sync`. The Tauri/React GUI stays in-tree untouched (deleting
it would buy nothing at runtime and cost every upstream merge).

## Branch discipline

- **`loki`** — the branch we run. Default branch of this fork. Carries the divergences below.
- **`main`** — pristine mirror of `upstream/main`. Never commit here.

## Divergence budget — exactly four sanctioned changes

Anything beyond these requires a wiki decision note in `~/loki/wiki/decisions/` first.

1. **`backend/requirements-loki.txt`** — Kokoro-only dependency set. Engine libs are
   lazy-imported, so the server boots and serves Kokoro without the other six engines'
   dependency minefield (git-only deps, custom find-links, `--no-deps` ordering hacks).
2. **`POST /generate/sync`** (`backend/routes/generate_sync.py` + 2 registration lines in
   `routes/__init__.py`) — synchronous, non-persisted TTS returning WAV bytes. Thin route over
   the pre-existing `services.generation.generate_audio_sync()` (which upstream orphaned).
   Tests: `backend/tests/test_generate_sync.py`.
3. **Registry-derived engine enums** — schema validation reads `TTS_ENGINES` instead of
   hand-copied `pattern=` regexes. (Candidate for an upstream PR — if accepted, this diff
   disappears.)
4. **Blended Kokoro voice profiles** (`services/profiles.py::_validate_profile_fields`) — accepts
   comma-delimited `preset_voice_id` combos (e.g. `"bm_george,bm_lewis"`) when every component is
   individually a valid preset. Kokoro's own `KPipeline.load_voice()` already averages
   comma-joined voice tensors; this only widens the one validator gate standing in front of it.
   No changes to the generation pipeline. Tests: `backend/tests/test_profiles_blend.py`.
   Rationale: `~/loki/wiki/decisions/voicebox-fork-divergence-4-blended-voice.md`.
   **Known limitation:** `PUT /profiles/{id}` does not let you change an existing preset
   profile's `preset_voice_id` (upstream re-reads it from the DB row, ignores the request body)
   — to change a preset profile's voice, delete and recreate it. Not patched — out of the
   decision note's scope; revisit if it becomes a recurring friction.

## Update ritual (rolling upstream in)

```bash
cd ~/loki/vendor/voicebox
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout loki && git merge main
# On conflict: our 4 divergences win in their own files; upstream wins everywhere else.
# Check database/migrations.py diffs by hand — upstream migrations are version-less
# idempotent column checks; we never modify upstream-owned tables (schema-ownership rule).
backend/venv/bin/python -m pytest backend/tests/ -q --deselect-heavy   # see below
git push origin loki
```

Then restart the daemon: `launchctl kickstart -k gui/$UID/com.loki.voicebox`.

## Running the test suite under requirements-loki.txt

Tests that require absent engines or model downloads are skipped/deselected:

```bash
backend/venv/bin/python -m pytest backend/tests/ -q \
  --ignore=backend/tests/test_all_models_e2e.py \
  --ignore=backend/tests/test_rocm_backends.py \
  --ignore=backend/tests/test_rocm_build.py \
  --ignore=backend/tests/test_rocm_download.py \
  --ignore=backend/tests/test_rocm_requirements.py \
  --ignore=backend/tests/test_package_rocm.py \
  --ignore=backend/tests/test_qwen_download.py \
  --ignore=backend/tests/test_amd_gpu_detect.py \
  --ignore=backend/tests/test_profile_duplicate_names.py
```

(`test_profile_duplicate_names.py` is broken on pristine upstream too — it sys.path-hacks
top-level imports that collide with the package layout; verified 2026-07-09, both invocation
modes. Not a fork regression.)

Known-failing under requirements-loki.txt (verified identical on pristine upstream, 2026-07-09 —
they exercise the transformers/HF offline-patch layer that the Kokoro-only dep set omits):
`test_offline_guard.py`, `test_offline_patch.py`, `test_progress.py` (10 cases). Baseline:
**90 passed, 10 failed** — any new failure beyond these is a real regression.

## Runtime prerequisites (macOS)

- `brew install espeak-ng` — the bundled `espeakng-loader` wheel points at a dead CI path;
  the daemon plist exports `PHONEMIZER_ESPEAK_LIBRARY` + `ESPEAK_DATA_PATH` to the brew install.
- Python 3.12 venv at `backend/venv` (created from `backend/requirements-loki.txt`).
- Kokoro weights lazy-download from HuggingFace on first generation (~330MB, cached in
  `~/.cache/huggingface`).

## What Loki does NOT use

- The personality/Qwen3 rewrite (`personality: true`) — Loki's `lib/loki_voice.py` owns text;
  this fork owns acoustics only. Never enable both.
- Stories, effects presets UI, channels, cloud sync, CUDA/ROCm binary management, captures
  hotkeys — GUI-serving surface, left untouched, never called.
