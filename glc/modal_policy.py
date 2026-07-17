"""Client boundary for policy decisions made by the isolated Modal worker."""

from __future__ import annotations

import logging
import os
from typing import Any

import modal

from glc.policy.schemas import PolicyVerdict

logger = logging.getLogger(__name__)


def evaluate_policy(tool_call: dict[str, Any], context: dict[str, Any]) -> PolicyVerdict:
    """Request a remote policy verdict and deny when the isolated worker is unavailable."""
    app_name = os.getenv("GLC_MODAL_POLICY_APP", "glc-v1-gateway")
    policy_function = modal.Function.from_name(app_name, "policy_evaluator")
    try:
        result = policy_function.remote(tool_call, context)
        return PolicyVerdict.model_validate(result)
    except Exception:
        logger.exception("Isolated policy evaluation failed")
        return PolicyVerdict(action="deny", reason="policy service unavailable")
