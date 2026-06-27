"""Cartesia Sonic TTS provider."""

from __future__ import annotations

import base64
import os

import httpx

from glc.voice.tts.base import SynthesizeResult, TTSError, TTSProvider
from glc.voice.tts.providers.cartesia.schemas import (
    CARTESIA_ENDPOINT,
    DEFAULT_MIME,
    DEFAULT_SAMPLE_RATE,
    CartesiaTTSRequest,
    build_headers,
    resolve_voice_id,
)

_CLIENT: httpx.AsyncClient | None = None


class Provider(TTSProvider):
    name = "cartesia"

    async def synthesize(self, text: str, voice_id: str | None = None) -> SynthesizeResult:
        mock = self.config.get("mock")
        if mock is not None:
            return await mock.synthesize(text, voice_id)

        if not text:
            return SynthesizeResult(
                audio_b64="",
                mime=DEFAULT_MIME,
                sample_rate=DEFAULT_SAMPLE_RATE,
                provider=self.name,
                cost_usd=0.0,
            )

        api_key = os.getenv("CARTESIA_API_KEY")
        if not api_key:
            raise TTSError("CARTESIA_API_KEY is not set")

        selected_voice_id = resolve_voice_id(voice_id, os.getenv("CARTESIA_VOICE_ID"))
        headers = build_headers(api_key)
        body = CartesiaTTSRequest(transcript=text, voice_id=selected_voice_id).to_dict()

        audio = bytearray()
        try:
            global _CLIENT
            if _CLIENT is None or _CLIENT.is_closed:
                _CLIENT = httpx.AsyncClient(timeout=30.0)
            async with _CLIENT.stream("POST", CARTESIA_ENDPOINT, headers=headers, json=body) as response:
                if response.is_error:
                    error_bytes = await response.aread()
                    error_message = error_bytes.decode("utf-8", errors="replace").strip()
                    raise TTSError(
                        f"Cartesia request failed: {error_message[:500] or response.reason_phrase}",
                        status=response.status_code,
                    )
                async for chunk in response.aiter_bytes():
                    audio.extend(chunk)
        except TTSError:
            raise
        except httpx.TimeoutException as exc:
            raise TTSError("Cartesia request timed out") from exc
        except httpx.RequestError as exc:
            raise TTSError(f"Cartesia request failed: {exc}") from exc

        if not audio:
            raise TTSError("Cartesia returned no audio", status=502)

        return SynthesizeResult(
            audio_b64=base64.b64encode(audio).decode("ascii"),
            mime=DEFAULT_MIME,
            sample_rate=DEFAULT_SAMPLE_RATE,
            provider=self.name,
            cost_usd=0.0,
        )
