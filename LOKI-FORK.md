# Loki Fork — modemax/voicebox

This is Loki's maintained fork of [jamiepine/voicebox](https://github.com/jamiepine/voicebox)
(KB-0358). Only the **backend** runs — headless, as a launchd daemon on Chris's Mac, serving
Loki's TTS through upstream's OpenAI-compatible `POST /v1/audio/speech`. The Tauri/React GUI stays in-tree untouched (deleting
it would buy nothing at runtime and cost every upstream merge).

## Branch discipline

- **`loki`** — the branch we run. Default branch of this fork. Carries the divergences below.
- **`main`** — pristine mirror of `upstream/main`. Never commit here.

## Divergence budget — exactly three sanctioned changes (#1, #3, #4)

Anything beyond these requires a wiki decision note in `~/loki/wiki/decisions/` first.

Numbers are stable on purpose (the decision notes and `KB-` cards cite them), so the retired
ones leave gaps rather than renumbering the rest. **#2 and #5 are retired, see below.**

1. **`backend/requirements-loki.txt`** — Kokoro-only dependency set. Engine libs are
   lazy-imported, so the server boots and serves Kokoro without the other six engines'
   dependency minefield (git-only deps, custom find-links, `--no-deps` ordering hacks).
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

### Retired divergences (KB-1204, 2026-11-08)

- **#2 `POST /generate/sync`** (`routes/generate_sync.py` + 2 registration lines in
  `routes/__init__.py`, tests `test_generate_sync.py`) — deleted. Upstream's `POST /v1/audio/speech`
  (`routes/openai_compat.py`) is the same contract: synchronous, persists nothing, WAV bytes back.
  Loki's clients moved to it on 2026-10-16 (Phase A); `routes/__init__.py` is byte-identical to
  upstream again.
- **#5 Effects chain on the synchronous path** (the additive `effects_chain` param + guarded
  `apply_effects()` call in `services/generation.py::generate_audio_sync`) — deleted.
  `_create_speech` applies the profile's stored `effects_chain` itself, before normalise, so the
  live "Loki" profile (highpass 85 Hz + compressor) is still shaped. `services/generation.py` is
  byte-identical to upstream again.
- Rationale: `~/loki/wiki/decisions/voicebox-fork-divergence-5-sync-effects.md` (marked
  Superseded). #2 was sanctioned with the fork itself (KB-0358) and never had its own note.
  **`/generate/sync` no longer exists: a client that still posts there gets a 404.**
- Verified before landing (KB-1204): boot on a spare port against a `sqlite3 .backup` copy of the
  live data dir; old route and new route agree on duration, loudness (-20.00 dBFS), spectral
  centroid and sub-85 Hz energy within run-to-run spread, and a no-effects control moves the
  centroid by ~300 Hz, so the comparison could have failed. Synthesis is non-deterministic: compare
  measurements against the same route's own spread, never bytes.

**Two trade-offs we accepted by retiring these** (Chris, 2026-10-30; the 15-minute health probe
`bin/check-voicebox-health.py` is the backstop for both):

1. *Effects-chain robustness.* The old route validated the chain and SKIPPED an invalid one (never
   a 500). Upstream's route catches only a JSON parse error, so an unknown effect, an out-of-range
   compressor ratio, or a dict where a list belongs is a **500 on every utterance**, which falls
   Loki to `macos_say`. Reachable only by editing the DB directly (`PUT /profiles` validates on
   save) or if a pedalboard/upstream change tightens a range. Re-read this after every merge that
   touches `utils/effects.py`.
2. *Request-time HuggingFace cache gate.* Upstream checks the model cache on EVERY request: an
   orphan `*.incomplete` blob or a missing `snapshots/` directory under
   `models--hexgrad--Kokoro-82M` returns 400 even when the model is already in RAM. The old route
   only needed the cache at load time.

**Smaller behavioural differences between the old route and upstream's** (audited, none matter to
Loki's clients, which branch only on HTTP 200 vs not): input cap **10,000 chars** (old: 50,000;
`lib/tts/synth.sh` documents why that costs nothing at Kokoro speeds); the synthesis language comes
from the *profile* (old: the request's `language`, default `en`; the live `Loki` profile stores
`en`); an unknown profile is 400 `voice_not_found` (old: 404) and a bad body is 400 (old: 422), in
OpenAI's `{"error": ...}` shape; no `seed` and no `X-Voicebox-Engine` / `X-Voicebox-Profile`
response headers; engine resolves through the model alias (`tts-1` = the profile's own engine).

**What now guards the voice.** The fork suite no longer has a test that the profile's effects chain
reaches the audio (the deleted `test_generate_sync.py` had the only three). Loki's
`tests/test_voicebox_effects_order.py` reads `routes/openai_compat.py` and fails if
`_create_speech` stops applying `profile.effects_chain` or applies it after `normalize_audio`.
After ANY upstream merge, boot-trial it (spare port, a `sqlite3 .backup` copy of the live data
dir) and compare against a no-effects control; a green suite alone does not prove the voice.

## Update ritual (rolling upstream in)

```bash
cd ~/loki/vendor/voicebox
git fetch upstream
git checkout main && git merge --ff-only upstream/main && git push origin main
git checkout loki && git merge main
# On conflict: our 3 divergences win in their own files; upstream wins everywhere else.
# Check database/migrations.py diffs by hand — upstream migrations are version-less
# idempotent column checks; we never modify upstream-owned tables (schema-ownership rule).
backend/venv/bin/python -m pytest backend/tests/ -q <the --ignore list below>
git push origin loki
```

Then restart the daemon: `~/loki/bin/restart-daemon.sh com.loki.voicebox`. After the 2026-10-16 merge the
DB runs in WAL mode — back it up with `sqlite3 ... .backup`, never a bare file copy.

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
  --ignore=backend/tests/test_profile_duplicate_names.py \
  --ignore=backend/tests/test_audioop_python313.py \
  --ignore=backend/tests/test_mlx_smoke.py
```

The last two arrived with the 2026-09-06 upstream merge and are the same class as the rest
(absent deps, not fork regressions), but the first one is worth knowing about because of
*how* it fails. `test_audioop_python313.py` imports `build_binary`, which imports
`PyInstaller` — a packaging dep this Kokoro-only venv has no reason to carry. That is a
**collection** error, not a test failure, so pytest aborts the entire run and exits **2**
having tested nothing at all. The tail of the output looks like a tidy warnings summary
and the wrapper's own exit code can read as 0. Check the count line, not the vibe: a run
that says `1 error in 3.01s` never ran.

(`test_profile_duplicate_names.py` is broken on pristine upstream too — it sys.path-hacks
top-level imports that collide with the package layout; verified 2026-07-09, both invocation
modes. Not a fork regression.)

Baseline as of KB-1204 (2026-11-08, on top of the 2026-10-16 v0.6.0 merge, KB-1195), run with the
`requirements-loki.txt` pins (`transformers<=4.57.6`, `huggingface_hub<1.0`) installed:
**276 passed, 2 failed, 6 skipped** (was 287 before the 11 `test_generate_sync.py` tests went
with divergences #2 and #5). The two failures are pre-existing on pristine upstream and
not fork regressions: `test_omnivoice_compat.py::test_declared_languages_are_resolvable_by_omnivoice`
(omnivoice is absent) and `test_progress.py::test_hf_progress_tracker`. Any new failure is real.

**Do not run this suite on an unpinned venv and call the failures "known".** Until KB-1195 this
file listed `test_offline_guard.py` / `test_offline_patch.py` / `test_progress.py` as harmless
"offline-patch layer" failures. After the 2026-10-04 upstream release the offline guard sits in
the model-load path, so on `transformers` 5.x (which `kokoro` pulls in) every synchronous
generation (then `/generate/sync`, now `/v1/audio/speech`) returns 500 — the same 17 tests, now a
live outage. A failing test list is a claim about a
dependency; re-read it on every merge.

Before landing any upstream merge, boot it: spare port, a `sqlite3 .backup` copy of the live data
dir, one real `POST /v1/audio/speech` (`{"model":"tts-1","input":...,"voice":"Loki","response_format":"wav"}`)
against the `Loki` profile.

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
