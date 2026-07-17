"""Append-only SQLite audit log.

Every channel message, agent decision, policy verdict, and tool dispatch
lands here. Append-only is enforced by SQLite triggers as well as the
application API. Every entry includes the previous entry's digest so deletion
or mutation is detected when the store opens.

Each append commits immediately so writes survive a hard kill.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(os.path.expanduser("~/.glc"))
GENESIS_HASH = "0" * 64
CHAINED_COLUMNS = (
    "ts",
    "session_id",
    "channel",
    "channel_user_id",
    "trust_level",
    "event_type",
    "tool",
    "policy_verdict",
    "params_json",
    "result_json",
)


def _resolve_path() -> str:
    """Resolve at call time, not import time, so tests that swap the env
    var see the change."""
    return os.getenv("GLC_AUDIT_DB", str(DEFAULT_DIR / "audit.sqlite"))


@contextmanager
def _conn():
    p = _resolve_path()
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, isolation_level=None)  # autocommit; each insert flushes
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _entry_hash(previous_hash: str, values: tuple[Any, ...]) -> str:
    """Bind one canonical audit record to the digest of the record before it."""
    canonical = json.dumps(
        [previous_hash, *values],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _verify_chain(c: sqlite3.Connection) -> None:
    """Refuse to open an audit store whose persisted hash chain was altered."""
    previous_hash = GENESIS_HASH
    columns = ", ".join(CHAINED_COLUMNS)
    for row in c.execute(f"SELECT {columns}, prev_hash, entry_hash FROM audit_log ORDER BY id"):
        values = tuple(row[column] for column in CHAINED_COLUMNS)
        expected_hash = _entry_hash(previous_hash, values)
        if row["prev_hash"] != previous_hash or row["entry_hash"] != expected_hash:
            raise RuntimeError("audit log hash-chain verification failed")
        previous_hash = expected_hash


def _migrate_to_hash_chain(c: sqlite3.Connection) -> None:
    """Upgrade legacy rows once, then install database-level append-only guards."""
    version_row = c.execute("SELECT MAX(version) AS version FROM audit_schema").fetchone()
    version = int(version_row["version"] or 0)
    c.execute("BEGIN IMMEDIATE")
    try:
        if version < 2:
            columns = {row["name"] for row in c.execute("PRAGMA table_info(audit_log)")}
            if "prev_hash" not in columns:
                c.execute("ALTER TABLE audit_log ADD COLUMN prev_hash TEXT")
            if "entry_hash" not in columns:
                c.execute("ALTER TABLE audit_log ADD COLUMN entry_hash TEXT")

            previous_hash = GENESIS_HASH
            selected_columns = ", ".join(CHAINED_COLUMNS)
            rows = c.execute(f"SELECT id, {selected_columns} FROM audit_log ORDER BY id").fetchall()
            for row in rows:
                values = tuple(row[column] for column in CHAINED_COLUMNS)
                current_hash = _entry_hash(previous_hash, values)
                c.execute(
                    "UPDATE audit_log SET prev_hash=?, entry_hash=? WHERE id=?",
                    (previous_hash, current_hash, row["id"]),
                )
                previous_hash = current_hash
            c.execute(
                "INSERT OR IGNORE INTO audit_schema (version, applied_at) VALUES (2, ?)",
                (time.time(),),
            )

        c.execute(
            """CREATE TRIGGER IF NOT EXISTS audit_log_reject_update
               BEFORE UPDATE ON audit_log
               BEGIN
                   SELECT RAISE(ABORT, 'audit_log is append-only');
               END"""
        )
        c.execute(
            """CREATE TRIGGER IF NOT EXISTS audit_log_reject_delete
               BEFORE DELETE ON audit_log
               BEGIN
                   SELECT RAISE(ABORT, 'audit_log is append-only');
               END"""
        )
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def init_store() -> None:
    """Create or migrate the audit store, then verify its complete history."""
    with _conn() as c:
        c.executescript(_SCHEMA_PATH.read_text())
        _migrate_to_hash_chain(c)
        _verify_chain(c)


def _jsonify(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, default=str)
    except Exception:
        return json.dumps({"_repr": repr(v)})


class AuditStore:
    """Application-layer write-once store. The class deliberately exposes
    no update or delete methods. Reads (for the replay viewer) live in
    query() which is read-only."""

    def append(
        self,
        *,
        channel: str,
        channel_user_id: str,
        trust_level: str,
        event_type: str,
        session_id: str | None = None,
        tool: str | None = None,
        policy_verdict: str | None = None,
        params: Any = None,
        result: Any = None,
    ) -> int:
        """Append one transactionally chained record without permitting history rewrites."""
        values = (
            time.time(),
            session_id,
            channel,
            channel_user_id,
            trust_level,
            event_type,
            tool,
            policy_verdict,
            _jsonify(params),
            _jsonify(result),
        )
        with _conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                previous = c.execute(
                    "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous["entry_hash"] if previous else GENESIS_HASH
                current_hash = _entry_hash(previous_hash, values)
                cur = c.execute(
                    """INSERT INTO audit_log
                       (ts, session_id, channel, channel_user_id, trust_level,
                        event_type, tool, policy_verdict, params_json, result_json,
                        prev_hash, entry_hash)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*values, previous_hash, current_hash),
                )
                c.execute("COMMIT")
                return int(cur.lastrowid or 0)
            except Exception:
                c.execute("ROLLBACK")
                raise


_singleton: AuditStore | None = None


def get_store() -> AuditStore:
    global _singleton
    if _singleton is None:
        init_store()
        _singleton = AuditStore()
    return _singleton


def append(**kwargs: Any) -> int:
    return get_store().append(**kwargs)


def query(limit: int = 100, session_id: str | None = None, channel: str | None = None) -> list[dict]:
    q = "SELECT * FROM audit_log"
    where, args = [], []
    if session_id:
        where.append("session_id=?")
        args.append(session_id)
    if channel:
        where.append("channel=?")
        args.append(channel)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def schema_version() -> int:
    with _conn() as c:
        row = c.execute("SELECT MAX(version) AS v FROM audit_schema").fetchone()
        return int(row["v"] or 0)
