"""V9-compatible per-call ledger. Same schema as llm_gatewayV9/db.py, but
the database lives under ~/.glc/ so the gateway is installable as a daemon
without writing into the source tree.

Note: this is the *worker call* ledger, used by /v1/cost/by_agent. The
audit log (every channel message, policy verdict, tool dispatch) is a
separate append-only store under glc/audit/store.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from glc.config import get_or_create_install_token, install_tenant_id

DEFAULT_DIR = Path(os.path.expanduser("~/.glc"))
DB_PATH = os.getenv("GLC_GATEWAY_DB", str(DEFAULT_DIR / "gateway.sqlite"))
MAX_TOKEN_COUNT = 10_000_000
LEDGER_FIELDS = (
    "ts",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "cache_create_tokens",
    "cache_read_tokens",
    "latency_ms",
    "status",
    "error",
    "prompt_chars",
    "response_chars",
    "override",
    "attempted",
    "tool_calls",
    "reasoning_applied",
    "tool_dialect",
    "call_role",
    "router_decision",
    "embed_dim",
    "agent",
    "session",
    "retries",
    "tenant",
)
SIGNED_ROW_PREDICATE = f"ledger_signature_valid({', '.join(LEDGER_FIELDS)}, signature)=1"


def _signing_key() -> bytes:
    """Load the gateway-only production writer key, deriving a local-only development key."""
    configured_key = os.getenv("GLC_LEDGER_SIGNING_KEY")
    if configured_key is not None:
        key = configured_key.encode()
        if len(key) < 32:
            raise RuntimeError("GLC_LEDGER_SIGNING_KEY must contain at least 32 bytes")
        return key
    if os.getenv("GLC_ENV", "development").lower() == "production":
        raise RuntimeError("GLC_LEDGER_SIGNING_KEY is required in production")
    local_material = f"glc-ledger-development:{get_or_create_install_token()}".encode()
    return hashlib.sha256(local_material).digest()


def _signature(values: tuple, key: bytes) -> str:
    """Sign the canonical values of a complete ledger row."""
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _bounded_integer(name: str, value: int, maximum: int) -> int:
    """Reject non-integral, negative, or implausibly large ledger measurements."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 0 and {maximum}")
    return value


def _required_label(name: str, value: str, maximum: int = 512) -> str:
    """Reject empty or oversized identity fields before they enter the signed ledger."""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value


def _ensure_parent() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def conn():
    _ensure_parent()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    signing_key = _signing_key()

    def _valid_signature(*values) -> int:
        """Expose constant-time HMAC verification to signed SQL read predicates."""
        *row_values, supplied_signature = values
        if not isinstance(supplied_signature, str):
            return 0
        try:
            expected_signature = _signature(tuple(row_values), signing_key)
        except (TypeError, ValueError):
            return 0
        return int(hmac.compare_digest(expected_signature, supplied_signature))

    c.create_function(
        "ledger_signature_valid",
        len(LEDGER_FIELDS) + 1,
        _valid_signature,
        deterministic=True,
    )
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_create_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                status TEXT,
                error TEXT,
                prompt_chars INTEGER DEFAULT 0,
                response_chars INTEGER DEFAULT 0,
                override TEXT,
                attempted TEXT,
                tool_calls INTEGER DEFAULT 0,
                reasoning_applied INTEGER DEFAULT 0,
                tool_dialect TEXT,
                call_role TEXT DEFAULT 'worker',
                router_decision TEXT,
                embed_dim INTEGER,
                agent TEXT,
                session TEXT,
                retries INTEGER DEFAULT 0,
                tenant TEXT,
                signature TEXT
            )"""
        )
        columns = {row["name"] for row in c.execute("PRAGMA table_info(calls)")}
        if "tenant" not in columns:
            c.execute("ALTER TABLE calls ADD COLUMN tenant TEXT")
        if "signature" not in columns:
            c.execute("ALTER TABLE calls ADD COLUMN signature TEXT")
        c.execute(
            "UPDATE calls SET tenant=? WHERE tenant IS NULL OR tenant=''",
            (install_tenant_id(),),
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON calls(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prov_ts ON calls(provider, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_role_ts ON calls(call_role, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_agent_ts ON calls(agent, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_session_ts ON calls(session, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tenant_ts ON calls(tenant, ts DESC)")


def log_call(
    provider,
    model,
    input_tokens=0,
    output_tokens=0,
    latency_ms=0,
    status="ok",
    error=None,
    prompt_chars=0,
    response_chars=0,
    override=None,
    attempted=None,
    cache_create_tokens=0,
    cache_read_tokens=0,
    tool_calls=0,
    reasoning_applied=False,
    tool_dialect=None,
    call_role="worker",
    router_decision=None,
    embed_dim=None,
    agent=None,
    session=None,
    retries=0,
    tenant=None,
) -> None:
    """Validate and sign one gateway-authored usage record before insertion."""
    provider = _required_label("provider", provider)
    model = _required_label("model", model)
    call_role = _required_label("call_role", call_role, maximum=128)
    if status not in {"ok", "error"}:
        raise ValueError("status must be 'ok' or 'error'")
    if not isinstance(reasoning_applied, bool):
        raise ValueError("reasoning_applied must be a boolean")

    input_tokens = _bounded_integer("input_tokens", input_tokens, MAX_TOKEN_COUNT)
    output_tokens = _bounded_integer("output_tokens", output_tokens, MAX_TOKEN_COUNT)
    cache_create_tokens = _bounded_integer(
        "cache_create_tokens", cache_create_tokens, MAX_TOKEN_COUNT
    )
    cache_read_tokens = _bounded_integer("cache_read_tokens", cache_read_tokens, MAX_TOKEN_COUNT)
    latency_ms = _bounded_integer("latency_ms", latency_ms, 86_400_000)
    prompt_chars = _bounded_integer("prompt_chars", prompt_chars, 1_000_000_000)
    response_chars = _bounded_integer("response_chars", response_chars, 1_000_000_000)
    tool_calls = _bounded_integer("tool_calls", tool_calls, 100_000)
    retries = _bounded_integer("retries", retries, 10_000)
    if embed_dim is not None:
        embed_dim = _bounded_integer("embed_dim", embed_dim, 10_000_000)

    authenticated_tenant = install_tenant_id()
    if tenant is not None and tenant != authenticated_tenant:
        raise ValueError("tenant must match the authenticated installation")
    values = (
        time.time(),
        provider,
        model,
        input_tokens,
        output_tokens,
        cache_create_tokens,
        cache_read_tokens,
        latency_ms,
        status,
        error,
        prompt_chars,
        response_chars,
        override,
        attempted,
        tool_calls,
        1 if reasoning_applied else 0,
        tool_dialect,
        call_role,
        router_decision,
        embed_dim,
        agent,
        session,
        retries,
        authenticated_tenant,
    )
    row_signature = _signature(values, _signing_key())
    with conn() as c:
        c.execute(
            """INSERT INTO calls (ts, provider, model, input_tokens, output_tokens,
                                  cache_create_tokens, cache_read_tokens,
                                  latency_ms, status, error, prompt_chars, response_chars,
                                  override, attempted, tool_calls, reasoning_applied, tool_dialect,
                                  call_role, router_decision, embed_dim,
                                  agent, session, retries, tenant, signature)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*values, row_signature),
        )


def by_agent(session=None, since=None, tenant=None):
    where = ["ts >= ?", SIGNED_ROW_PREDICATE]
    # Day-rollover fix: bucket by calendar day, not by 24h window.
    args = [since if since is not None else (time.time() - (time.time() % 86400))]
    if session:
        where.append("session=?")
        args.append(session)
    if tenant:
        where.append("tenant=?")
        args.append(tenant)
    q = (
        "SELECT agent, provider, COUNT(*) AS calls, "
        "SUM(input_tokens) AS in_tok, SUM(output_tokens) AS out_tok, "
        "SUM(latency_ms) AS total_latency_ms, "
        "SUM(retries) AS total_retries, "
        "SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok, "
        "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
        "FROM calls WHERE " + " AND ".join(where) + " AND agent IS NOT NULL "
        "GROUP BY agent, provider"
    )
    with conn() as c:
        rows = c.execute(q, args).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["agent"], []).append(dict(r))
        return out


def recent(limit=100, provider=None, status=None, tenant=None):
    q = "SELECT * FROM calls"
    where, args = [SIGNED_ROW_PREDICATE], []
    if provider:
        where.append("provider=?")
        args.append(provider)
    if status:
        where.append("status=?")
        args.append(status)
    if tenant:
        where.append("tenant=?")
        args.append(tenant)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
        for row in rows:
            row.pop("tenant", None)
            row.pop("signature", None)
        return rows


def aggregate(call_role=None):
    now = time.time()
    day_start = now - (now % 86400)
    q = f"""SELECT provider,
                  COUNT(*) AS calls,
                  SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok_calls,
                  SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                  SUM(input_tokens) AS in_tok,
                  SUM(output_tokens) AS out_tok,
                  SUM(cache_read_tokens) AS cache_reads,
                  SUM(cache_create_tokens) AS cache_creates,
                  SUM(tool_calls) AS tool_calls,
                  AVG(latency_ms) AS avg_latency,
                  MAX(ts) AS last_ts
             FROM calls WHERE ts >= ? AND {SIGNED_ROW_PREDICATE}"""
    args = [day_start]
    if call_role == "worker":
        q += " AND (call_role='worker' OR call_role IS NULL)"
    elif call_role == "router":
        q += " AND call_role LIKE 'router%'"
    elif call_role:
        q += " AND call_role=?"
        args.append(call_role)
    q += " GROUP BY provider"
    with conn() as c:
        rows = c.execute(q, args).fetchall()
        return {r["provider"]: dict(r) for r in rows}
