"""No-dependency browser UI for manually testing GLC's Cartesia route.

The browser talks to this local stdlib HTTP server. The server proxies requests
to the running GLC gateway so the page avoids CORS and never sees the Cartesia
API key.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>GLC Cartesia Demo</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; max-width: 900px; }
    textarea, input { width: 100%; box-sizing: border-box; font: inherit; margin: 8px 0 16px; padding: 10px; }
    textarea { height: 130px; }
    button { padding: 10px 16px; font: inherit; cursor: pointer; }
    pre { background: #111827; color: #e5e7eb; padding: 16px; overflow: auto; }
    audio { width: 100%; margin-top: 16px; }
  </style>
</head>
<body>
  <h1>GLC Cartesia TTS Demo</h1>
  <p>This page calls local GLC with <code>prefer="streaming"</code>, which routes to the Cartesia provider.</p>
  <label>Text</label>
  <textarea id="text">Indeed! We can also change the voice profiles easily. This third sentence is spoken by a male voice profile to demonstrate that the voice I D parameter is fully wired and functioning correctly.</textarea>
  <label>Voice ID (optional)</label>
  <input id="voiceId" placeholder="Leave blank to use CARTESIA_VOICE_ID or provider default" />
  <button id="speak">Generate with Cartesia</button>
  <audio id="audio" controls></audio>
  <pre id="meta">Ready.</pre>
  <script>
    const textEl = document.getElementById("text");
    const voiceEl = document.getElementById("voiceId");
    const audioEl = document.getElementById("audio");
    const metaEl = document.getElementById("meta");
    document.getElementById("speak").onclick = async () => {
      const started = performance.now();
      metaEl.textContent = "Calling local GLC...";
      const response = await fetch("/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: textEl.value, voice_id: voiceEl.value || null })
      });
      const payload = await response.json();
      if (!response.ok) {
        metaEl.textContent = JSON.stringify(payload, null, 2);
        return;
      }
      const bytes = Uint8Array.from(atob(payload.audio_b64), c => c.charCodeAt(0));
      audioEl.src = URL.createObjectURL(new Blob([bytes], { type: payload.mime }));
      await audioEl.play();
      metaEl.textContent = JSON.stringify({
        provider: payload.provider,
        mime: payload.mime,
        sample_rate: payload.sample_rate,
        audio_bytes: bytes.length,
        total_latency_ms: Math.round(performance.now() - started)
      }, null, 2);
    };
  </script>
</body>
</html>
"""


def build_speak_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON sent from the demo UI server to GLC's `/v1/speak` route."""
    return {
        "text": str(data.get("text") or ""),
        "voice_id": data.get("voice_id") or None,
        "prefer": "streaming",
    }


def proxy_speak(glc_url: str, payload: dict[str, Any]) -> tuple[int, bytes]:
    """Forward one UI request to GLC using only Python's standard library."""
    request = urllib.request.Request(
        f"{glc_url.rstrip('/')}/v1/speak",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class CartesiaDemoHandler(BaseHTTPRequestHandler):
    """Serve the demo page and proxy same-origin `/speak` requests to GLC."""

    glc_url = "http://127.0.0.1:8111"

    def do_GET(self) -> None:
        """Serve the single HTML page."""
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """Proxy UI synthesis requests to GLC while keeping API keys server-side."""
        if self.path != "/speak":
            self.send_error(404)
            return
        content_length = int(self.headers.get("Content-Length") or "0")
        try:
            data = json.loads(self.rfile.read(content_length) or b"{}")
            status, body = proxy_speak(self.glc_url, build_speak_payload(data))
        except Exception as exc:
            status = 502
            body = json.dumps({"error": str(exc)}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep demo output focused on explicit startup and request status lines."""
        return


def main() -> None:
    """Start the local browser demo server."""
    parser = argparse.ArgumentParser(description="Serve a no-dependency Cartesia demo UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8120)
    parser.add_argument("--glc-url", default=os.getenv("GLC_URL", "http://127.0.0.1:8111"))
    args = parser.parse_args()
    CartesiaDemoHandler.glc_url = args.glc_url
    server = ThreadingHTTPServer((args.host, args.port), CartesiaDemoHandler)
    print(f"Cartesia demo UI: http://{args.host}:{args.port}")
    print(f"Proxying to GLC: {CartesiaDemoHandler.glc_url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
