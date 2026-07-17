"""Wrap the unchanged GLC FastAPI gateway for deployment on Modal."""

import logging
import os
from pathlib import Path

import modal

app = modal.App("glc-v1-gateway")
logger = logging.getLogger(__name__)

BASE_IMAGE = (
    "python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba"
)
UV_VERSION = "0.11.3"

SUPPORTED_PROVIDER_SLOTS = ("gemini", "nvidia", "groq", "cerebras", "openrouter", "github")
PROVIDER_SLOTS = tuple(
    name
    for name in dict.fromkeys(
        slot.strip().lower()
        for slot in os.getenv("GLC_MODAL_PROVIDER_SLOTS", "gemini").split(",")
        if slot.strip()
    )
    if name in SUPPORTED_PROVIDER_SLOTS
)

SANDBOX_EGRESS_DOMAINS = tuple(
    dict.fromkeys(
        domain.strip().lower()
        for domain in os.getenv("GLC_SANDBOX_EGRESS_ALLOWLIST", "").split(",")
        if domain.strip()
    )
)
TOOL_CREDENTIAL_SLOTS = frozenset(
    slot.strip().lower() for slot in os.getenv("GLC_TOOL_CREDENTIAL_SLOTS", "").split(",") if slot.strip()
)
sandbox_image = modal.Image.from_registry(BASE_IMAGE)

image = (
    modal.Image.from_registry(BASE_IMAGE)
    .uv_sync(
        frozen=True,
        uv_version=UV_VERSION,
        extra_options="--no-dev",
    )
    .env(
        {
            "GLC_ENV": "production",
            "GLC_CONFIG_DIR": "/data/glc",
            "GLC_GATEWAY_DB": "/data/glc/gateway.sqlite",
            "GLC_AUDIT_DB": "/data/glc/audit.sqlite",
            "GLC_PAIRING_DB": "/data/glc/pairings.sqlite",
            "GLC_MODAL_PROVIDER_MODE": "1",
            "GLC_MODAL_PROVIDER_APP": "glc-v1-gateway",
            "GLC_MODAL_PROVIDER_SLOTS": ",".join(PROVIDER_SLOTS),
            "GLC_MODAL_POLICY_MODE": "1",
            "GLC_MODAL_POLICY_APP": "glc-v1-gateway",
        }
    )
    .add_local_dir(str(Path(__file__).parent / "glc"), remote_path="/root/glc")
)

data_volume = modal.Volume.from_name("glc-data", create_if_missing=True)
policy_volume = modal.Volume.from_name("glc-policy", create_if_missing=True)
policy_image = image.env({"GLC_CONFIG_DIR": "/policy"})


class _CommittingASGIApp:
    """Persist SQLite changes before externally visible ASGI work completes.

    Modal Volumes periodically snapshot changes, but explicit commits keep the
    audit trail durable at request, WebSocket-message, and shutdown boundaries.
    """

    def __init__(self, web, volume):
        self._web = web
        self._volume = volume

    async def __call__(self, scope, receive, send):
        """Delegate to FastAPI while committing the mounted volume before completion."""

        async def send_after_commit(message):
            """Commit writes before a response or lifecycle boundary becomes observable."""
            message_type = message["type"]
            should_commit = (
                (message_type == "http.response.body" and not message.get("more_body", False))
                or message_type in {
                    "websocket.send",
                    "websocket.close",
                    "lifespan.shutdown.complete",
                }
            )
            if should_commit:
                await self._volume.commit.aio()
            await send(message)

        await self._web(scope, receive, send_after_commit)


@app.function(image=policy_image, volumes={"/policy": policy_volume})
def policy_evaluator(tool_call: dict, context: dict) -> dict:
    """Evaluate policy in a secretless process that is isolated from caller monkey patches."""
    from glc.config import policy_yaml_path
    from glc.policy.engine import PolicyEngine

    policy_volume.reload()
    engine = PolicyEngine.from_yaml(policy_yaml_path())
    return engine.evaluate(tool_call, context).model_dump()


async def _execute_provider_slot(slot: str, payload: dict) -> dict:
    """Run one provider operation inside the only function allowed to hold that slot's key."""
    from glc.cache import GeminiCache
    from glc.embedders import EmbedderError, GeminiEmbedder
    from glc.providers import (
        CerebrasProvider,
        GeminiProvider,
        GitHubProvider,
        GroqProvider,
        NvidiaProvider,
        OpenRouterProvider,
        ProviderError,
    )

    try:
        action = payload["action"]
        configured_model = payload["configured_model"]
        if action == "embed":
            embedder = GeminiEmbedder(os.environ["GEMINI_API_KEY"], configured_model)
            result = await embedder.embed(payload["text"], payload["task_type"])
            return {"ok": True, "result": result}

        provider_classes = {
            "gemini": lambda: GeminiProvider(
                os.environ["GEMINI_API_KEY"], configured_model, GeminiCache(ttl_seconds=300)
            ),
            "nvidia": lambda: NvidiaProvider(os.environ["NVIDIA_API_KEY"], configured_model),
            "groq": lambda: GroqProvider(os.environ["GROQ_API_KEY"], configured_model),
            "cerebras": lambda: CerebrasProvider(os.environ["CEREBRAS_API_KEY"], configured_model),
            "openrouter": lambda: OpenRouterProvider(os.environ["OPEN_ROUTER_API_KEY"], configured_model),
            "github": lambda: GitHubProvider(os.environ["GITHUB_ACCESS_TOKEN"], configured_model),
        }
        provider = provider_classes[slot]()
        if action == "stream":
            chunks = [
                chunk async for chunk in provider.stream(payload["messages"], **payload.get("kwargs", {}))
            ]
            return {"ok": True, "chunks": chunks}
        result = await provider.chat(payload["messages"], **payload.get("kwargs", {}))
        return {"ok": True, "result": result}
    except (ProviderError, EmbedderError) as exc:
        logger.exception("Provider slot %s request failed", slot)
        return {
            "ok": False,
            "error": str(exc),
            "status": getattr(exc, "status", None),
            "retryable": getattr(exc, "retryable", True),
        }
    except Exception:
        logger.exception("Unexpected provider slot %s failure", slot)
        return {"ok": False, "error": "provider worker failed", "status": 502, "retryable": True}


if "gemini" in PROVIDER_SLOTS:

    @app.function(image=image, secrets=[modal.Secret.from_name("glc-provider-gemini")])
    async def gemini_provider(payload: dict) -> dict:
        """Run Gemini chat and embedding requests with only the Gemini credential."""
        return await _execute_provider_slot("gemini", payload)


if "nvidia" in PROVIDER_SLOTS:

    @app.function(image=image, secrets=[modal.Secret.from_name("glc-provider-nvidia")])
    async def nvidia_provider(payload: dict) -> dict:
        """Run NVIDIA requests with only the NVIDIA credential."""
        return await _execute_provider_slot("nvidia", payload)


if "groq" in PROVIDER_SLOTS:

    @app.function(image=image, secrets=[modal.Secret.from_name("glc-provider-groq")])
    async def groq_provider(payload: dict) -> dict:
        """Run Groq requests with only the Groq credential."""
        return await _execute_provider_slot("groq", payload)


if "cerebras" in PROVIDER_SLOTS:

    @app.function(image=image, secrets=[modal.Secret.from_name("glc-provider-cerebras")])
    async def cerebras_provider(payload: dict) -> dict:
        """Run Cerebras requests with only the Cerebras credential."""
        return await _execute_provider_slot("cerebras", payload)


if "openrouter" in PROVIDER_SLOTS:

    @app.function(image=image, secrets=[modal.Secret.from_name("glc-provider-openrouter")])
    async def openrouter_provider(payload: dict) -> dict:
        """Run OpenRouter requests with only the OpenRouter credential."""
        return await _execute_provider_slot("openrouter", payload)


if "github" in PROVIDER_SLOTS:

    @app.function(image=image, secrets=[modal.Secret.from_name("glc-provider-github")])
    async def github_provider(payload: dict) -> dict:
        """Run GitHub Models requests with only the GitHub credential."""
        return await _execute_provider_slot("github", payload)


@app.function(timeout=90)
def run_untrusted_component(
    command: list[str],
    credential_slot: str | None = None,
) -> dict[str, str | int]:
    """Issue one approved tool credential only to its short-lived, egress-limited Sandbox."""
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValueError("command must contain one or more non-empty arguments")
    normalized_slot = credential_slot.strip().lower() if credential_slot else None
    if normalized_slot and normalized_slot not in TOOL_CREDENTIAL_SLOTS:
        raise ValueError("credential slot is not approved for tool execution")

    sandbox_secrets = [modal.Secret.from_name(f"glc-tool-{normalized_slot}")] if normalized_slot else []

    sandbox = modal.Sandbox.create(
        *command,
        app=app,
        image=sandbox_image,
        timeout=60,
        cpu=0.5,
        memory=512,
        secrets=sandbox_secrets,
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
    secrets=[modal.Secret.from_name("glc-gateway-auth")],
    min_containers=0,
    max_containers=1,
)
@modal.asgi_app(requires_proxy_auth=True)
def fastapi_app():
    """Expose the durable gateway while keeping its auth secret out of persisted storage."""
    os.makedirs("/data/glc", exist_ok=True)
    Path("/data/glc/install_token").unlink(missing_ok=True)

    from glc.main import app as web

    return _CommittingASGIApp(web, data_volume)
