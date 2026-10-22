"""Persist a read-only enable proof; cache positive eligibility for at most 30s."""

import asyncio
import json
import os
import tempfile
import time

from engine.community.plugins.dws_hosting.database import HostingDatabase


class BackendHostingRollout:
    def __init__(self, state_dir, binding, verifier, *, clock=time.monotonic):
        self.path = state_dir / "rollout-proof.json"
        self.binding, self.verifier, self.clock = binding, verifier, clock
        self.until = 0.0
        self._lock = asyncio.Lock()

    async def remember(self, payload, signature):
        async with self._lock:
            self.until = 0.0
            await asyncio.to_thread(self._write, payload, signature)

    def _write(self, payload, signature):
        self._scope(payload)
        HostingDatabase._check_private(self.path.parent, directory=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=self.path.parent, delete=False
        ) as stream:
            temporary = stream.name
            try:
                json.dump({"payload": payload, "signature": signature}, stream)
                stream.flush()
                os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def _scope(self, payload):
        value = json.loads(payload)
        if (
            (value.get("bot_id"), value.get("actor_id"))
            != (self.binding.bot_id, self.binding.owner_id)
            or value.get("operation") != "enabled"
            or value.get("data", {}).get("enabled") is not True
        ):
            raise ValueError("invalid_rollout_scope")

    def _read(self):
        # COSEC: private bounded proof, no symlink following; only Backend can
        # validate it. This proof cannot execute or replay a management command.
        HostingDatabase._check_private(self.path, directory=False)
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(25001)
        if len(raw) > 25000:
            raise ValueError("invalid_rollout_proof")
        proof = json.loads(raw)
        self._scope(proof["payload"])
        return proof

    async def allowed(self):
        async with self._lock:
            if self.clock() < self.until:
                return True
            self.until = 0.0
            started = self.clock()
            try:
                proof = await asyncio.to_thread(self._read)
                allowed = await self.verifier.runtime_eligible(**proof)
            except Exception:
                return False
            if allowed is not True:
                return False
            self.until = started + 30
            return self.clock() < self.until
