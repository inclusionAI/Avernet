"""Public-local fixture only: no Engine, real Bot, LLM, Swarm or sandbox claims.

Applies strict persona file.edit patches into its own persistent fixture store;
never moves active and deliberately refuses all other patch kinds.
"""

import hmac
import json
import sqlite3
from datetime import UTC, datetime

from avernet_genome.canonical import canonicalize, revision_id, sha256_digest
from avernet_genome.models import (
    BotRef,
    GenomeRevision,
    Provenance,
    ProvenanceKind,
    RevisionMetadata,
    RevisionStatus,
)
from avernet_genome.spec_models import GenomePolicy, GenomeSpec
from avernet_genome.text_edits import apply_edits

from .core.evolution_run.budget import Budget
from .core.evolution_run.ports import BotPolicy
from .core.evolution_strategy import InMemoryStrategyRegistry, check_allowed_genes, registration_from_json
from .core.evolution_strategy.strategies.identity_hardening import IdentityHardening
from .plugin_api import AccessGrant, Platform

BOT = BotRef("local-entity", "local-bot", "local-user", "openclaw", "personal", "user")
STRATEGY = "local/identity-hardening@1.0.0"


class LocalAccess:
    def __init__(self, token: str):
        if len(token) < 32:
            raise ValueError("local token must have at least 32 characters")
        self.token = token

    async def authorize(self, *, bot_id, owner_hint, credentials, write):
        expected = f"Bearer {self.token}"
        if not hmac.compare_digest(credentials.get("authorization", ""), expected):
            raise PermissionError("invalid local credential")
        if bot_id != BOT.bot_id or owner_hint != BOT.owner_id:
            raise PermissionError("local profile serves only its fixture Bot")
        return AccessGrant(BOT, BOT.owner_id)


class LocalGenome:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript("""
          CREATE TABLE IF NOT EXISTS revisions (id TEXT PRIMARY KEY, spec TEXT NOT NULL,
            parent TEXT, created_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS blobs (digest TEXT PRIMARY KEY, data BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS fixture_refs (name TEXT PRIMARY KEY, revision TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS submissions (key TEXT PRIMARY KEY, revision TEXT NOT NULL);
        """)
        self.policy = GenomePolicy.from_json({"mutable_genes": ["persona"], "pins": [], "risk_overrides": {}})
        if self.conn.execute("SELECT 1 FROM fixture_refs WHERE name='active'").fetchone() is None:
            digest = self._blob(b"# Local fixture Bot\n\nA reproducible 01-06 demonstration.\n")
            spec = GenomeSpec.from_json(
                {
                    "persona": [{"type": "SOUL.md", "digest": digest}],
                    "skills": [],
                    "memory": None,
                    "resources": [],
                    "tools": {"mcp": []},
                    "engine_config": {},
                    "script": None,
                }
            )
            rev = self._revision(spec, None)
            self.conn.execute("INSERT INTO fixture_refs VALUES ('active', ?)", (rev,))
            self.conn.commit()

    def _check(self, bot):
        if bot != BOT:
            raise PermissionError("not the local fixture Bot")

    def _blob(self, data):
        digest = sha256_digest(data)
        self.conn.execute("INSERT OR IGNORE INTO blobs VALUES (?, ?)", (digest, data))
        return digest

    def _revision(self, spec, parent):
        rid = revision_id(spec, self.policy)
        self.conn.execute(
            "INSERT OR IGNORE INTO revisions VALUES (?, ?, ?, ?)",
            (rid, json.dumps(spec.to_json()), parent, datetime.now(UTC).isoformat()),
        )
        return rid

    async def drift(self, bot):
        self._check(bot)
        # Fixture has no external Engine state to drift.

    async def resolve_revision(self, bot, ref):
        self._check(bot)
        if ref == "active":
            ref = self.conn.execute("SELECT revision FROM fixture_refs WHERE name='active'").fetchone()[0]
        row = self.conn.execute(
            "SELECT rowid, spec, parent, created_at FROM revisions WHERE id=?", (ref,)
        ).fetchone()
        if row is None:
            raise KeyError(ref)
        return GenomeRevision(
            1,
            RevisionMetadata(
                ref,
                row[0],
                row[2],
                Provenance(ProvenanceKind.USER, "local-fixture"),
                datetime.fromisoformat(row[3]),
                RevisionStatus.DRAFT if row[2] is None else RevisionStatus.CANDIDATE,
                None,
            ),
            GenomeSpec.from_json(json.loads(row[1])),
            self.policy,
        )

    async def read_content(self, bot, digest):
        self._check(bot)
        row = self.conn.execute("SELECT data FROM blobs WHERE digest=?", (digest,)).fetchone()
        if row is None:
            raise FileNotFoundError(digest)
        return row[0]

    async def snapshot_files(self, revision, *, bot):
        self._check(bot)
        return {
            f"persona/{entry.type}": await self.read_content(bot, entry.digest)
            for entry in revision.spec.persona
        }

    async def commit_patch(self, bot, patch, run_id, strategy_ref, allowed_genes, created_by):
        self._check(bot)
        problems = check_allowed_genes(patch, list(allowed_genes), self.policy.pins)
        if problems:
            raise ValueError("; ".join(problems))
        key = run_id + ":" + sha256_digest(canonicalize(patch.to_json()))
        found = self.conn.execute("SELECT revision FROM submissions WHERE key=?", (key,)).fetchone()
        if found:
            return found[0]
        parent = await self.resolve_revision(bot, patch.base)
        files = await self.snapshot_files(parent, bot=bot)
        # Validate all ops before any persistence. This fixture intentionally does
        # not implement Skills, resource or Registry authorization policy.
        for op in patch.ops:
            if op.op != "file.edit" or op.target not in files:
                raise ValueError("local fixture supports existing persona file.edit only")
            files[op.target] = apply_edits(files[op.target].decode(), op.edits).encode()
        try:
            spec = parent.spec.to_json()
            for item in spec["persona"]:
                item["digest"] = self._blob(files[f"persona/{item['type']}"])
            rid = self._revision(GenomeSpec.from_json(spec), patch.base)
            self.conn.execute("INSERT INTO submissions VALUES (?, ?)", (key, rid))
            self.conn.commit()
            return rid
        except BaseException:
            self.conn.rollback()
            raise

    async def move_ref(self, bot, ref, revision_id, actor):
        raise NotImplementedError("promotion is outside the 01-06 local fixture")


class LocalGovernance:
    async def engine(self, bot):
        return bot.engine_type

    async def policy(self, bot):
        return BotPolicy(("persona",), ())

    async def bot_ceiling(self, bot):
        return Budget(max_usd=1, max_wall_clock_s=300)

    async def tenant_ceiling(self, bot):
        return await self.bot_ceiling(bot)

    async def profile_problems(self, profile):
        return [] if profile == "pending@1" else ["local profile only supports pending@1 (no evaluation)"]


class LocalKills:
    async def strategy_disabled(self, strategy, version):
        return False

    async def bot_frozen(self, bot):
        return False

    async def paused(self):
        return False


class LocalExperience:
    def for_bot(self, bot):
        if bot != BOT:
            raise PermissionError("not the fixture Bot")
        return self

    async def sessions(self, *, days, limit=500, revision=None):
        if days < 1 or not 1 <= limit <= 500:
            raise ValueError("invalid experience bounds")
        # Explicit fixture evidence, NOT an exported conversation or real Bot log.
        return (
            [{"episode_id": "local-fixture-episode", "source": "fixture"}][:limit] if revision is None else []
        )

    async def feedback(self, **kwargs):
        raise NotImplementedError("local feedback is not implemented")


class LocalPlatform(Platform):
    async def close(self):
        self.genome.conn.close()


async def build_local_platform(settings):
    registry = InMemoryStrategyRegistry()
    await registry.register(
        registration_from_json(
            {
                "id": "local/identity-hardening",
                "version": "1.0.0",
                "runtime": {"kind": "in_process", "package": "avernet_evolution.local"},
                "needs": {"experience.sessions@1": {}},
                "params_schema": {"type": "object", "additionalProperties": False},
            }
        ),
        {},
    )
    return LocalPlatform(
        LocalAccess(settings.local_token_file.read_text().strip()),
        LocalGenome(f"{settings.database}.fixture"),
        LocalGovernance(),
        LocalKills(),
        LocalExperience(),
        registry,
        {STRATEGY: lambda: IdentityHardening(max_verdict_polls=0)},
    )
