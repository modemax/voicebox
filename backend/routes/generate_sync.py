"""POST /generate/sync — synchronous, non-persisted TTS for local agents.

The stock ``/generate`` is built for the studio UI: enqueue, persist to
the generations table, poll for status. An always-on assistant's alert
path wants the opposite contract — block briefly, get audio bytes back,
leave no history behind. This route is a thin HTTP face over
:func:`services.generation.generate_audio_sync`, which already implements
exactly that (in-memory, no DB writes, no generations-dir output).

Profile resolution matches ``/speak``: explicit name/id, then the
caller's ``X-Voicebox-Client-Id`` binding, then the default playback
voice. Engine validation is registry-derived (``TTS_ENGINES``), not a
hand-copied enum — an engine added to the registry is speakable here
with no schema edit.

Loki-fork divergence #2 of 3 — see LOKI-FORK.md at the repo root.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..mcp_server.resolve import resolve_profile
from ..services import generation as generation_service

logger = logging.getLogger(__name__)

router = APIRouter()


class SyncGenerateRequest(BaseModel):
    """Request body for POST /generate/sync."""

    text: str = Field(..., min_length=1, max_length=50000)
    profile: Optional[str] = Field(
        default=None,
        description=(
            "Profile name (case-insensitive) or id. Falls back to the "
            "caller's X-Voicebox-Client-Id binding, then the default "
            "playback voice."
        ),
    )
    engine: Optional[str] = Field(
        default=None,
        description=(
            "TTS engine key. Defaults to the resolved profile's "
            "default_engine / preset_engine."
        ),
    )
    language: str = Field(default="en", min_length=2, max_length=10)
    model_size: str = Field(default="default")
    seed: Optional[int] = None
    normalize: bool = True


@router.post("/generate/sync")
async def generate_sync(
    data: SyncGenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    """Generate speech synchronously and return WAV bytes in the response."""
    client_id = request.headers.get("X-Voicebox-Client-Id")
    profile = resolve_profile(data.profile, client_id, db)
    if profile is None:
        if data.profile:
            raise HTTPException(
                status_code=404,
                detail=f"Voice profile '{data.profile}' not found.",
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "No voice profile resolved. Pass `profile` (name or id), "
                "or bind a default via X-Voicebox-Client-Id."
            ),
        )

    engine = (
        data.engine
        or getattr(profile, "default_engine", None)
        or getattr(profile, "preset_engine", None)
        or "qwen"
    )

    # Registry-derived validation — the single source of truth for which
    # engines exist, instead of a duplicated pattern= enum.
    from ..backends import TTS_ENGINES

    if engine not in TTS_ENGINES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown engine '{engine}'. "
                f"Available: {', '.join(sorted(TTS_ENGINES))}"
            ),
        )

    try:
        wav_bytes = await generation_service.generate_audio_sync(
            profile_id=profile.id,
            text=data.text,
            language=data.language,
            engine=engine,
            model_size=data.model_size,
            seed=data.seed,
            normalize=data.normalize,
        )
    except HTTPException:
        raise
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - engine/runtime failures
        logger.exception("Sync generation failed (engine=%s)", engine)
        raise HTTPException(
            status_code=500, detail=f"Generation failed: {exc}"
        ) from exc

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Voicebox-Engine": engine,
            "X-Voicebox-Profile": str(profile.name),
        },
    )
