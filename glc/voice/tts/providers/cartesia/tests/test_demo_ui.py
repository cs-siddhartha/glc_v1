"""Internal tests for the no-dependency Cartesia demo UI helpers."""

from __future__ import annotations

from glc.voice.tts.providers.cartesia.demo.ui import HTML_PAGE, build_speak_payload


def test_demo_ui_forces_cartesia_streaming_prefer() -> None:
    """The browser demo should always route GLC `/v1/speak` calls to Cartesia."""
    payload = build_speak_payload({"text": "hello", "voice_id": "voice-123"})

    assert payload == {"text": "hello", "voice_id": "voice-123", "prefer": "streaming"}


def test_demo_ui_keeps_key_out_of_browser_html() -> None:
    """The page must not expose Cartesia credentials or ask users to paste API keys."""
    assert "CARTESIA_API_KEY" not in HTML_PAGE
    assert "/speak" in HTML_PAGE
