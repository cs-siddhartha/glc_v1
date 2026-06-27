"""Provider-local schema helpers for Cartesia's frozen Sonic bytes request.

The adapter has to send one exact wire shape to Cartesia. Keeping the constants
and request builders here makes the real path, demo tooling, and internal tests
share the same contract without touching shared gateway code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CARTESIA_ENDPOINT = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2025-04-16"
CARTESIA_MODEL_ID = "sonic-2"
DEFAULT_VOICE_ID = "694f9389-aac1-45b6-b726-9d9369183238"
DEFAULT_SAMPLE_RATE = 24000
DEFAULT_MIME = "audio/wav"


@dataclass(frozen=True)
class CartesiaVoice:
    """Represent Cartesia's id-based voice selector used by the bytes endpoint."""

    id: str
    mode: str = "id"

    def to_dict(self) -> dict[str, str]:
        """Return the JSON shape Cartesia expects under the `voice` field."""
        return {"mode": self.mode, "id": self.id}


@dataclass(frozen=True)
class CartesiaOutputFormat:
    """Represent the WAV/PCM output format required by the assignment guide."""

    container: str = "wav"
    encoding: str = "pcm_s16le"
    sample_rate: int = DEFAULT_SAMPLE_RATE

    def to_dict(self) -> dict[str, str | int]:
        """Return the JSON shape Cartesia expects under `output_format`."""
        return {
            "container": self.container,
            "encoding": self.encoding,
            "sample_rate": self.sample_rate,
        }


@dataclass(frozen=True)
class CartesiaTTSRequest:
    """Build the Cartesia Sonic request body from transcript and voice id."""

    transcript: str
    voice_id: str
    model_id: str = CARTESIA_MODEL_ID
    output_format: CartesiaOutputFormat = CartesiaOutputFormat()

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON body sent to `POST /tts/bytes`."""
        return {
            "model_id": self.model_id,
            "transcript": self.transcript,
            "voice": CartesiaVoice(self.voice_id).to_dict(),
            "output_format": self.output_format.to_dict(),
        }


def resolve_voice_id(voice_id: str | None, env_voice_id: str | None = None) -> str:
    """Resolve caller override, environment override, then documented default voice id."""
    return voice_id or env_voice_id or DEFAULT_VOICE_ID


def build_headers(api_key: str) -> dict[str, str]:
    """Build auth/version headers required by Cartesia's API."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Cartesia-Version": CARTESIA_VERSION,
        "Content-Type": "application/json",
    }
