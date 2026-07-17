"""Loads channels.yaml and policy.yaml. Resolves user-config directory.

The default config lives in `~/.glc/`. Override with GLC_CONFIG_DIR for
tests and CI. The directory is created on import if missing.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import yaml

DEFAULT_DIR = Path(os.path.expanduser("~/.glc"))
CONFIG_DIR = Path(os.getenv("GLC_CONFIG_DIR", str(DEFAULT_DIR)))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Packaged defaults shipped with glc (under the policy/ subpackage).
PACKAGED_POLICY = Path(__file__).parent / "policy" / "policy.yaml"
PACKAGED_CHANNELS = Path(__file__).parent / "channels.yaml"


def policy_yaml_path() -> Path:
    user = CONFIG_DIR / "policy.yaml"
    return user if user.exists() else PACKAGED_POLICY


def channels_yaml_path() -> Path:
    user = CONFIG_DIR / "channels.yaml"
    return user if user.exists() else PACKAGED_CHANNELS


def load_channels() -> dict:
    p = channels_yaml_path()
    if not p.exists():
        return {"channels": {}}
    return yaml.safe_load(p.read_text()) or {"channels": {}}


def install_token_path() -> Path:
    return CONFIG_DIR / "install_token"


def get_or_create_install_token() -> str:
    """Load production auth from the process Secret, with a local file fallback for development."""
    configured_token = os.getenv("GLC_INSTALL_TOKEN")
    if configured_token is not None:
        configured_token = configured_token.strip()
        if not configured_token:
            raise RuntimeError("GLC_INSTALL_TOKEN must not be empty")
        return configured_token
    if os.getenv("GLC_ENV", "development").lower() == "production":
        raise RuntimeError("GLC_INSTALL_TOKEN is required in production")

    p = install_token_path()
    if p.exists():
        return p.read_text().strip()
    import secrets

    tok = secrets.token_urlsafe(32)
    p.write_text(tok)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return tok


def install_tenant_id() -> str:
    """Derive a stable tenant scope without storing or exposing the installation token itself."""
    token = get_or_create_install_token()
    return hashlib.sha256(token.encode()).hexdigest()
