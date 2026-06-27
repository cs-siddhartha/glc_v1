"""Real-path Cartesia smoke demo for implementation PR videos.

The scored CI tests exercise only `config["mock"]`. This module runs the
provider without a mock, writes playable WAV files outside the repo by default,
and prints timing/metadata that proves the real Cartesia wire path works.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from glc.voice.tts.providers.cartesia.adapter import Provider
from glc.voice.tts.providers.cartesia.schemas import DEFAULT_VOICE_ID

SENTENCE_1 = (
    "Hello! This is the Cartesia Sonic text-to-speech provider running through the GLC gateway "
    "adapter. The adapter is using the real Cartesia upstream service, not the offline mock."
)

SENTENCE_2 = (
    "This second sentence demonstrates repeated synthesis through the same provider process. "
    "The adapter keeps the Cartesia request shape stable while reusing its HTTP client between calls."
)

SENTENCE_3 = (
    "Indeed! We can also change the voice profiles easily. This third sentence is spoken "
    "by a male voice profile to demonstrate that the voice I D parameter is fully wired "
    "and functioning correctly."
)


def write_audio(audio_b64: str, output_path: Path) -> int:
    """Decode a `SynthesizeResult.audio_b64` field into a WAV file for playback."""
    wav_bytes = base64.b64decode(audio_b64)
    output_path.write_bytes(wav_bytes)
    return len(wav_bytes)


async def run_step(provider: Provider, step: str, text: str, voice_id: str, output_path: Path) -> float:
    """Run one real Cartesia synthesis and print the fields reviewers need to see."""
    print("=============================================================")
    print(step)
    print(f"-> Voice ID: {voice_id}")
    start_time = time.perf_counter()
    result = await provider.synthesize(text, voice_id=voice_id)
    duration = time.perf_counter() - start_time
    audio_bytes = write_audio(result.audio_b64, output_path)
    print(f"-> Saved: {output_path.resolve()}")
    print(f"-> Time elapsed: {duration:.3f} seconds")
    print(f"-> Audio bytes: {audio_bytes}")
    print(f"-> Sample Rate: {result.sample_rate} Hz, Provider: {result.provider}, Cost: ${result.cost_usd:.2f}")
    return duration


async def main() -> None:
    """Drive three Cartesia real-path calls: baseline, repeated call, and explicit voice override."""
    load_dotenv()
    if not os.getenv("CARTESIA_API_KEY"):
        raise SystemExit("CARTESIA_API_KEY is not set")

    output_dir = Path(os.getenv("CARTESIA_DEMO_DIR", "/tmp/glc_cartesia_demo"))
    output_dir.mkdir(parents=True, exist_ok=True)

    default_voice_id = os.getenv("CARTESIA_VOICE_ID") or DEFAULT_VOICE_ID
    male_voice_id = os.getenv("CARTESIA_MALE_VOICE_ID") or default_voice_id
    provider = Provider(config={})

    duration1 = await run_step(
        provider,
        "Step 1: Running first Cartesia synthesis with the default voice...",
        SENTENCE_1,
        default_voice_id,
        output_dir / "demo_step1_cartesia_default.wav",
    )
    duration2 = await run_step(
        provider,
        "Step 2: Running second synthesis to demonstrate repeated real upstream calls...",
        SENTENCE_2,
        default_voice_id,
        output_dir / "demo_step2_cartesia_repeat.wav",
    )
    if duration2:
        print(f"-> Relative speed: {duration1 / duration2:.1f}x compared with step 1")

    await run_step(
        provider,
        "Step 3: Running third synthesis with an explicit voice_id override...",
        SENTENCE_3,
        male_voice_id,
        output_dir / "demo_step3_cartesia_voice_override.wav",
    )

    if male_voice_id == default_voice_id:
        print("Note: set CARTESIA_MALE_VOICE_ID to demonstrate a distinct male voice profile.")

    print("=============================================================")
    print(f"Verification complete. Play the saved WAV files from: {output_dir.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
