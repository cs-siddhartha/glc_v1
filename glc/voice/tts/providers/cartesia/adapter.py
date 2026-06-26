"""Cartesia Sonic TTS provider."""

from __future__ import annotations

import base64
import os

import httpx

from glc.voice.tts.base import SynthesizeResult, TTSError, TTSProvider

_ENDPOINT = "https://api.cartesia.ai/tts/bytes"
_DEFAULT_VOICE_ID = "694f9389-aac1-45b6-b726-9d9369183238"


class Provider(TTSProvider):
    name = "cartesia"

    async def synthesize(self, text: str, voice_id: str | None = None) -> SynthesizeResult:
        mock = self.config.get("mock")
        if mock is not None:
            return await mock.synthesize(text, voice_id)

        if not text:
            return SynthesizeResult(
                audio_b64="",
                mime="audio/wav",
                sample_rate=24000,
                provider=self.name,
                cost_usd=0.0,
            )

        api_key = os.getenv("CARTESIA_API_KEY")
        if not api_key:
            raise TTSError("CARTESIA_API_KEY is not set")

        selected_voice_id = voice_id or os.getenv("CARTESIA_VOICE_ID") or _DEFAULT_VOICE_ID
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Cartesia-Version": "2025-04-16",
            "Content-Type": "application/json",
        }
        body = {
            "model_id": "sonic-2",
            "transcript": text,
            "voice": {"mode": "id", "id": selected_voice_id},
            "output_format": {
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": 24000,
            },
        }

        audio = bytearray()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", _ENDPOINT, headers=headers, json=body) as response:
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
            mime="audio/wav",
            sample_rate=24000,
            provider=self.name,
            cost_usd=0.0,
        )
