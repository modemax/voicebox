"""Tests for blended Kokoro voice profiles (loki fork divergence #4).

``_validate_profile_fields`` is a pure function — no DB, no torch — so these
run fast and don't need the heavy fixtures. Locks: single-voice behavior is
unchanged, valid multi-voice combos are accepted, an unknown component in a
combo still fails with a useful error, and engines with no preset registry
(qwen/luxtts/etc.) are untouched.

See wiki/decisions/voicebox-fork-divergence-4-blended-voice.md for the
rationale — Kokoro's own KPipeline.load_voice() already blends comma-
delimited voice ids by averaging their tensors; this only widens the one
validator gate that stood between a stored profile and that capability.
"""

import pytest

from backend.services.profiles import _validate_profile_fields


def test_single_kokoro_voice_still_valid():
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="bm_george",
        design_prompt=None,
        default_engine="kokoro",
    )
    assert err is None


def test_single_unknown_kokoro_voice_still_rejected():
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="zz_nobody",
        design_prompt=None,
        default_engine="kokoro",
    )
    assert err is not None
    assert "zz_nobody" in err


def test_two_voice_blend_accepted():
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="bm_george,bm_lewis",
        design_prompt=None,
        default_engine="kokoro",
    )
    assert err is None


def test_blend_with_whitespace_around_comma_accepted():
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="bm_george, bm_lewis",
        design_prompt=None,
        default_engine="kokoro",
    )
    assert err is None


def test_blend_with_one_unknown_component_rejected_and_names_it():
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="bm_george,not_a_voice",
        design_prompt=None,
        default_engine="kokoro",
    )
    assert err is not None
    assert "not_a_voice" in err
    assert "bm_george" not in err.split("unknown component(s):")[1]


def test_three_voice_blend_accepted():
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine="kokoro",
        preset_voice_id="bm_george,bm_lewis,am_michael",
        design_prompt=None,
        default_engine="kokoro",
    )
    assert err is None


@pytest.mark.parametrize("engine", ["qwen", "luxtts", "chatterbox", "tada"])
def test_engines_with_no_preset_registry_unaffected(engine):
    # _get_preset_voice_ids returns an empty set for these — the "if
    # available_voice_ids" guard should skip validation entirely, same as
    # before this change (an engine with no catalog can't reject anything).
    err = _validate_profile_fields(
        voice_type="preset",
        preset_engine=engine,
        preset_voice_id="whatever,also_whatever",
        design_prompt=None,
        default_engine=engine,
    )
    assert err is None
