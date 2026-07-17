"""Wrap the unchanged GLC FastAPI gateway for deployment on Modal."""

import os
from pathlib import Path

import modal

app = modal.App("glc-v1-gateway")

SANDBOX_EGRESS_DOMAINS = tuple(
    dict.fromkeys(
        domain.strip().lower()
        for domain in os.getenv("GLC_SANDBOX_EGRESS_ALLOWLIST", "").split(",")
        if domain.strip()
    )
)
sandbox_image = modal.Image.debian_slim(python_version="3.11")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
        "httpx>=0.27",
        "python-dotenv>=1.0",
        "pydantic>=2.6",
        "jsonschema>=4.21",
        "pyyaml>=6.0",
        "websockets>=12.0",
    )
    .env(
        {
            "GLC_ENV": "production",
            "GLC_CONFIG_DIR": "/data/glc",
            "GLC_GATEWAY_DB": "/data/glc/gateway.sqlite",
            "GLC_AUDIT_DB": "/data/glc/audit.sqlite",
            "GLC_PAIRING_DB": "/data/glc/pairings.sqlite",
        }
    )
    .add_local_dir(str(Path(__file__).parent / "glc"), remote_path="/root/glc")
)

data_volume = modal.Volume.from_name("glc-data", create_if_missing=True)
llm_secret = modal.Secret.from_name("glc-llm-keys")


@app.function(timeout=90)
def run_untrusted_component(command: list[str]) -> dict[str, str | int]:
    """Execute untrusted component commands without gateway secrets, storage, or unrestricted egress."""
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must contain one or more non-empty arguments")

    sandbox = modal.Sandbox.create(
        *command,
        app=app,
        image=sandbox_image,
        timeout=60,
        cpu=0.5,
        memory=512,
        outbound_domain_allowlist=list(SANDBOX_EGRESS_DOMAINS),
    )
    try:
        stdout = sandbox.stdout.read()
        stderr = sandbox.stderr.read()
        exit_code = sandbox.wait()
        return {
            "stdout": stdout[:100_000],
            "stderr": stderr[:100_000],
            "exit_code": exit_code,
        }
    finally:
        sandbox.terminate()


@app.function(
    image=image,
    volumes={"/data": data_volume},
    secrets=[llm_secret],
    min_containers=0,
)
@modal.asgi_app(requires_proxy_auth=True)
def fastapi_app():
    """Expose the existing app while keeping its routes and lifespan unchanged."""
    os.makedirs("/data/glc", exist_ok=True)

    from glc.main import app as web

    return web
