"""Persist deployment identities before consuming any durable jobs.

A restart may not silently rotate worker signing material or redefine an already
registered strategy version. Intentional changes need a new strategy version;
key rotation needs a drained database and an explicit operator migration.
"""

import hashlib
import json
from dataclasses import asdict


def _encode(value):
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest()}
    raise TypeError("unsupported strategy registration value")


async def validate_identities(store, platform, signing_key: bytes, *, control_scope: str):
    identities = {
        "worker-signing-key": hashlib.sha256(signing_key).hexdigest(),
        "control-scope": hashlib.sha256(control_scope.encode()).hexdigest(),
    }
    refs = set()
    for record in await platform.registry.list():
        registration = record.registration
        ref = f"{registration.id}@{registration.version}"
        refs.add(ref)
        material = {
            "registration": asdict(registration),
            "definitions": [asdict(item) for item in record.definitions],
            "worker_image": (
                registration.runtime.image
                if registration.runtime.kind == "job_worker"
                else platform.worker_image
            ),
        }
        identities[f"strategy:{ref}"] = hashlib.sha256(
            json.dumps(material, sort_keys=True, default=_encode).encode()
        ).hexdigest()
    unresolved = store.conn.execute(
        "SELECT DISTINCT strategy || '@' || strategy_version AS ref FROM evolution_run "
        "WHERE status IN ('queued', 'running')"
    ).fetchall()
    if any(row["ref"] not in refs or row["ref"] not in platform.strategies for row in unresolved):
        raise ValueError("outstanding run strategy is missing from the deployment")
    store.conn.execute(
        "CREATE TABLE IF NOT EXISTS evolution_identity (name TEXT PRIMARY KEY, digest TEXT NOT NULL)"
    )
    store.conn.commit()
    store.conn.execute("BEGIN IMMEDIATE")
    try:
        for name, digest in identities.items():
            row = store.conn.execute("SELECT digest FROM evolution_identity WHERE name=?", (name,)).fetchone()
            if row is not None and row["digest"] != digest:
                raise ValueError(f"immutable deployment identity changed: {name}")
            store.conn.execute("INSERT OR IGNORE INTO evolution_identity VALUES (?, ?)", (name, digest))
        store.conn.commit()
    except BaseException:
        store.conn.rollback()
        raise
