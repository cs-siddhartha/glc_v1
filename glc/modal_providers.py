"""Secretless provider proxies for the Modal deployment."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import modal

from glc.embedders import EmbedderError, EmbeddingProvider, EmbedRateState

WORKER_MODELS = {
    "gemini": "gemini-2.5-flash",
    "nvidia": "deepseek-ai/deepseek-v3.2",
    "groq": "openai/gpt-oss-120b",
    "cerebras": "zai-glm-4.7",
    "openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
    "github": "openai/gpt-4.1-mini",
}

ROUTER_MODELS = {
    "cerebras": "llama3.1-8b",
    "groq": "llama-3.3-70b-versatile",
    "nvidia": "nvidia/llama-3.1-nemotron-nano-8b-v1",
    "github": "microsoft/Phi-4-mini-instruct",
}

MODEL_ENV = {
    "gemini": "GEMINI_MODEL",
    "nvidia": "NVIDIA_MODEL",
    "groq": "GROQ_MODEL",
    "cerebras": "CEREBRAS_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "github": "GITHUB_MODEL",
}

ROUTER_MODEL_ENV = {
    "cerebras": "ROUTER_CEREBRAS_MODEL",
    "groq": "ROUTER_GROQ_MODEL",
    "nvidia": "ROUTER_NVIDIA_MODEL",
    "github": "ROUTER_GITHUB_MODEL",
}


def _plain(value: Any) -> Any:
    """Convert Pydantic request values into objects accepted by Modal serialization."""
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


class ModalProviderProxy:
    """Preserve the local provider contract without placing its credential in the gateway."""

    def __init__(self, name: str, model: str, capabilities: dict[str, Any]):
        self.name = name
        self.model = model
        self.capabilities = capabilities
        app_name = os.getenv("GLC_MODAL_PROVIDER_APP", "glc-v1-gateway")
        self._function = modal.Function.from_name(app_name, f"{name}_provider")

    async def _invoke(self, action: str, messages: list[dict], **kwargs: Any) -> dict:
        """Send one serializable request to the credential-bearing provider slot."""
        from glc.providers import ProviderError

        payload = {
            "action": action,
            "configured_model": self.model,
            "messages": _plain(messages),
            "kwargs": _plain(kwargs),
        }
        envelope = await self._function.remote.aio(payload)
        if not envelope.get("ok"):
            raise ProviderError(
                envelope.get("error", "provider worker failed"),
                status=envelope.get("status"),
                retryable=envelope.get("retryable", True),
            )
        return envelope

    async def chat(self, messages: list[dict], **kwargs: Any) -> dict:
        """Execute a non-streaming provider call in the isolated slot."""
        envelope = await self._invoke("chat", messages, **kwargs)
        return envelope["result"]

    async def stream(self, messages: list[dict], **kwargs: Any) -> AsyncIterator[str]:
        """Relay isolated streaming chunks while keeping credentials out of this process."""
        envelope = await self._invoke("stream", messages, **kwargs)
        for chunk in envelope["chunks"]:
            yield chunk


class ModalGeminiEmbedder(EmbeddingProvider):
    """Route Gemini embedding calls through the same Gemini-only credential slot."""

    name = "gemini"

    def __init__(self, model: str):
        self.model = model
        self.state = EmbedRateState(rpm=5, cooldown=5.0)
        app_name = os.getenv("GLC_MODAL_PROVIDER_APP", "glc-v1-gateway")
        self._function = modal.Function.from_name(app_name, "gemini_provider")

    async def embed(self, text: str, task_type: str) -> dict:
        """Invoke Gemini embeddings without exposing its API key to the gateway."""
        envelope = await self._function.remote.aio(
            {
                "action": "embed",
                "configured_model": self.model,
                "text": text,
                "task_type": task_type,
            }
        )
        if not envelope.get("ok"):
            raise EmbedderError(
                envelope.get("error", "embedding worker failed"),
                status=envelope.get("status"),
            )
        return envelope["result"]


def build_modal_providers(*, router: bool = False) -> dict[str, ModalProviderProxy]:
    """Build proxies only for provider slots declared in the Modal deployment."""
    from glc import providers as provider_module

    configured = {
        name.strip() for name in os.getenv("GLC_MODAL_PROVIDER_SLOTS", "").split(",") if name.strip()
    }
    defaults = ROUTER_MODELS if router else WORKER_MODELS
    model_env = ROUTER_MODEL_ENV if router else MODEL_ENV
    out: dict[str, ModalProviderProxy] = {}
    for name, default_model in defaults.items():
        if name not in configured:
            continue
        model = os.getenv(model_env[name], default_model)
        capabilities = provider_module.model_capabilities(name, model, {})
        provider_class = {
            "gemini": provider_module.GeminiProvider,
            "nvidia": provider_module.NvidiaProvider,
            "groq": provider_module.GroqProvider,
            "cerebras": provider_module.CerebrasProvider,
            "openrouter": provider_module.OpenRouterProvider,
            "github": provider_module.GitHubProvider,
        }[name]
        capabilities = provider_module.model_capabilities(
            name,
            model,
            getattr(provider_class, "capabilities", {}),
        )
        out[name] = ModalProviderProxy(name, model, capabilities)
    return out


def build_modal_embedders() -> tuple[list[EmbeddingProvider], list[str]]:
    """Expose Gemini embedding only when its isolated provider slot is deployed."""
    configured = {
        name.strip() for name in os.getenv("GLC_MODAL_PROVIDER_SLOTS", "").split(",") if name.strip()
    }
    if "gemini" not in configured:
        return [], []
    model = os.getenv("EMBED_FALLBACK_MODEL", "gemini-embedding-001")
    embedder = ModalGeminiEmbedder(model)
    return [embedder], [embedder.name]
