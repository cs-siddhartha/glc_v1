"""Internal tests for Cartesia request schema helpers."""

from __future__ import annotations

from glc.voice.tts.providers.cartesia.schemas import (
    CARTESIA_MODEL_ID,
    CARTESIA_VERSION,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VOICE_ID,
    CartesiaTTSRequest,
    build_headers,
    resolve_voice_id,
)


def test_request_body_matches_frozen_cartesia_shape() -> None:
    """The provider must send the frozen Sonic bytes JSON shape."""
    body = CartesiaTTSRequest(transcript="hello", voice_id="voice-123").to_dict()

    assert body == {
        "model_id": CARTESIA_MODEL_ID,
        "transcript": "hello",
        "voice": {"mode": "id", "id": "voice-123"},
        "output_format": {
            "container": "wav",
            "encoding": "pcm_s16le",
            "sample_rate": DEFAULT_SAMPLE_RATE,
        },
    }


def test_voice_resolution_prefers_call_then_env_then_default() -> None:
    """Voice selection must preserve caller override, then env override, then provider default."""
    assert resolve_voice_id("call-voice", "env-voice") == "call-voice"
    assert resolve_voice_id(None, "env-voice") == "env-voice"
    assert resolve_voice_id(None, None) == DEFAULT_VOICE_ID


def test_headers_include_auth_and_frozen_version() -> None:
    """Cartesia rejects requests without bearer auth and the pinned API version."""
    headers = build_headers("secret")

    assert headers["Authorization"] == "Bearer secret"
    assert headers["Cartesia-Version"] == CARTESIA_VERSION
    assert headers["Content-Type"] == "application/json"
