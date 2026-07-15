"""Tests for POST /generate/sync (loki fork).

Locks the route's contract: profile resolution mirrors /speak, engine
validation is registry-derived (TTS_ENGINES, not a hand-copied enum),
and the response is raw WAV bytes with engine/profile echo headers.
The generation service itself is mocked — model inference is covered
by test_all_models_e2e.py.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.routes.generate_sync import router
from backend.database import get_db


FAKE_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


def _profile(**overrides):
    base = dict(
        id="prof-123",
        name="Loki",
        default_engine=None,
        preset_engine="kokoro",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def _fake_db():
        yield None

    app.dependency_overrides[get_db] = _fake_db
    return TestClient(app)


@pytest.fixture()
def client():
    return _build_client()


async def _fake_generate(**kwargs):
    return FAKE_WAV


class TestGenerateSyncHappyPath:
    def test_returns_wav_bytes_with_echo_headers(self, client):
        with (
            patch(
                "backend.routes.generate_sync.resolve_profile",
                return_value=_profile(),
            ),
            patch(
                "backend.services.generation.generate_audio_sync",
                side_effect=_fake_generate,
            ),
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "Voicebox lives.", "profile": "Loki"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/wav")
        assert resp.content == FAKE_WAV
        assert resp.headers["x-voicebox-engine"] == "kokoro"
        assert resp.headers["x-voicebox-profile"] == "Loki"

    def test_engine_resolution_prefers_explicit_over_profile(self, client):
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return FAKE_WAV

        with (
            patch(
                "backend.routes.generate_sync.resolve_profile",
                return_value=_profile(default_engine="luxtts"),
            ),
            patch(
                "backend.services.generation.generate_audio_sync",
                side_effect=_capture,
            ),
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "x", "profile": "Loki", "engine": "kokoro"},
            )
        assert resp.status_code == 200
        assert captured["engine"] == "kokoro"


class TestGenerateSyncEffectsChain:
    """Divergence #5: the resolved profile's effects_chain reaches the service."""

    def test_profile_effects_chain_is_parsed_and_passed(self, client):
        import json as _json

        chain = [{"type": "reverb", "params": {"room_size": 0.4}, "enabled": True}]
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return FAKE_WAV

        with (
            patch(
                "backend.routes.generate_sync.resolve_profile",
                return_value=_profile(effects_chain=_json.dumps(chain)),
            ),
            patch(
                "backend.services.generation.generate_audio_sync",
                side_effect=_capture,
            ),
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "x", "profile": "Loki"},
            )
        assert resp.status_code == 200
        assert captured["effects_chain"] == chain

    def test_no_effects_chain_passes_none(self, client):
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return FAKE_WAV

        with (
            patch(
                "backend.routes.generate_sync.resolve_profile",
                return_value=_profile(),  # no effects_chain attribute set
            ),
            patch(
                "backend.services.generation.generate_audio_sync",
                side_effect=_capture,
            ),
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "x", "profile": "Loki"},
            )
        assert resp.status_code == 200
        assert captured["effects_chain"] is None

    def test_unparseable_effects_chain_degrades_to_none(self, client):
        captured = {}

        async def _capture(**kwargs):
            captured.update(kwargs)
            return FAKE_WAV

        with (
            patch(
                "backend.routes.generate_sync.resolve_profile",
                return_value=_profile(effects_chain="{not valid json"),
            ),
            patch(
                "backend.services.generation.generate_audio_sync",
                side_effect=_capture,
            ),
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "x", "profile": "Loki"},
            )
        assert resp.status_code == 200
        assert captured["effects_chain"] is None


class TestGenerateSyncErrors:
    def test_unknown_named_profile_is_404(self, client):
        with patch(
            "backend.routes.generate_sync.resolve_profile", return_value=None
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "x", "profile": "NoSuchVoice"},
            )
        assert resp.status_code == 404

    def test_no_profile_resolvable_is_400(self, client):
        with patch(
            "backend.routes.generate_sync.resolve_profile", return_value=None
        ):
            resp = client.post("/generate/sync", json={"text": "x"})
        assert resp.status_code == 400

    def test_unknown_engine_is_422_and_lists_registry(self, client):
        with patch(
            "backend.routes.generate_sync.resolve_profile",
            return_value=_profile(),
        ):
            resp = client.post(
                "/generate/sync",
                json={"text": "x", "profile": "Loki", "engine": "not_an_engine"},
            )
        assert resp.status_code == 422
        assert "kokoro" in resp.json()["detail"]

    def test_empty_text_is_422(self, client):
        resp = client.post("/generate/sync", json={"text": "", "profile": "Loki"})
        assert resp.status_code == 422

    def test_service_value_error_maps_to_400(self, client):
        async def _boom(**kwargs):
            raise ValueError("preset profile only supports engine 'kokoro'")

        with (
            patch(
                "backend.routes.generate_sync.resolve_profile",
                return_value=_profile(),
            ),
            patch(
                "backend.services.generation.generate_audio_sync",
                side_effect=_boom,
            ),
        ):
            resp = client.post(
                "/generate/sync", json={"text": "x", "profile": "Loki"}
            )
        assert resp.status_code == 400


class TestEngineRegistryIsSchemaSource:
    """The registry, not a duplicated pattern enum, defines valid engines."""

    def test_every_registry_engine_passes_validation(self, client):
        from backend.backends import TTS_ENGINES

        async def _ok(**kwargs):
            return FAKE_WAV

        for engine in TTS_ENGINES:
            with (
                patch(
                    "backend.routes.generate_sync.resolve_profile",
                    return_value=_profile(preset_engine=None),
                ),
                patch(
                    "backend.services.generation.generate_audio_sync",
                    side_effect=_ok,
                ),
            ):
                resp = client.post(
                    "/generate/sync",
                    json={"text": "x", "profile": "Loki", "engine": engine},
                )
            assert resp.status_code == 200, f"engine {engine} rejected"
