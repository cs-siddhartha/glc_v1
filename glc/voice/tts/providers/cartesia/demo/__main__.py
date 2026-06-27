"""Module entry point for Cartesia demos inside the owned provider path."""

from __future__ import annotations

import argparse
import asyncio

from glc.voice.tts.providers.cartesia.demo import smoke, ui


def main() -> None:
    """Select either the real-path smoke demo or the browser UI demo."""
    parser = argparse.ArgumentParser(description="Run Cartesia demo helpers.")
    parser.add_argument("mode", choices=("smoke", "ui"), nargs="?", default="ui")
    args, rest = parser.parse_known_args()
    if args.mode == "smoke":
        asyncio.run(smoke.main())
        return
    import sys

    sys.argv = [sys.argv[0], *rest]
    ui.main()


if __name__ == "__main__":
    main()
