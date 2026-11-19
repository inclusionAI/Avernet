"""Public platform plugin conformance: no Backend implementation on sys.path."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from avernet_genome.canonical import sha256_digest

from avernet_evolution.core.evolution_run.container_host import _default_command
from avernet_evolution.local import BOT, LocalGenome
from avernet_evolution.plugins.kubernetes_worker import WORKER_COMMAND, KubernetesJobWorker
from avernet_evolution.plugins.platform_http import BotHttp, HttpGenome, PlatformCredential, PlatformHttpError
from avernet_evolution.plugins.session_http import HttpSessionExperience


async def headers(bot):
    assert bot == BOT
    return PlatformCredential({"Authorization": "Bearer test-only"}, "delegating-member")


def ok(data):
    return httpx.Response(200, json={"code": 200000, "data": data})


@pytest.mark.asyncio
async def test_scope_envelope_redirect_and_no_ambient_cookies():
    calls = []

    def handler(req):
        calls.append(req)
        return ok({"ok": True})

    async with httpx.AsyncClient(
        base_url="http://platform.test", transport=httpx.MockTransport(handler)
    ) as c:
        bridge = BotHttp(c, headers)
        assert await bridge.request(BOT, "GET", "/sessions", params={"user_id": "other"}) == {"ok": True}
        assert await bridge.request(BOT, "GET", "") == {"ok": True}
        assert calls[-1].url.path == f"/openapi/v1/bots/{BOT.bot_id}"
        req = calls[0]
        assert req.url.params["user_id"] == "delegating-member"
        assert req.url.params["entity_id"] == BOT.entity_id
        assert "owner_id" not in req.url.params
        assert req.url.path == f"/openapi/v1/bots/{BOT.bot_id}/sessions"
        for suffix in ("https://other.test", "/../escape", "/sessions?user_id=other"):
            with pytest.raises(ValueError):
                await bridge.request(BOT, "GET", suffix)
        c.cookies.set("private", "must-not-cross-scopes")
        with pytest.raises(PlatformHttpError, match="ambient cookies"):
            await bridge.request(BOT, "GET", "/sessions")
        with pytest.raises(ValueError, match="ambient cookies"):
            BotHttp(c, headers)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "http://other.test/private"}),
        httpx.Response(200, json={"code": 403000, "data": "private-body"}),
        httpx.Response(200, content=b"not-json-private-body"),
        httpx.Response(200, content=b"a" * (5 * 1024 * 1024 + 1)),
    ],
)
@pytest.mark.asyncio
async def test_http_failures_never_report_private_response(response):
    async with httpx.AsyncClient(
        base_url="http://platform.test", transport=httpx.MockTransport(lambda r: response)
    ) as c:
        with pytest.raises(PlatformHttpError) as exc:
            await BotHttp(c, headers).request(BOT, "GET", "/sessions")
        assert "private" not in str(exc.value)


@pytest.mark.asyncio
async def test_genome_checks_identity_digest_and_snapshot(tmp_path):
    fixture = LocalGenome(tmp_path / "genome.db")
    revision = await fixture.resolve_revision(BOT, "active")
    wire = revision.as_record(BOT)
    content = await fixture.read_content(BOT, revision.spec.persona[0].digest)

    def handle(req):
        return httpx.Response(200, content=content) if "/content/" in req.url.path else ok(wire)

    async def drift(bot):
        return {"observed": "explicit-provider"}

    async with httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(handle)) as c:
        genome = HttpGenome(BotHttp(c, headers), drift)
        read = await genome.resolve_revision(BOT, "active")
        assert read.as_record(BOT) == wire
        assert await genome.snapshot_files(read, bot=BOT) == {"persona/SOUL.md": content}
        assert await genome.drift(BOT) == {"observed": "explicit-provider"}
        with pytest.raises(PlatformHttpError, match="content digest"):
            await genome.read_content(BOT, sha256_digest(b"other"))
        wire["revision"]["bot"]["owner_id"] = "other"
        with pytest.raises(PlatformHttpError, match="identity"):
            await genome.resolve_revision(BOT, "active")
        wire["revision"]["bot"]["owner_id"] = BOT.owner_id
        wire["revision"]["id"] = sha256_digest(b"other")
        with pytest.raises(PlatformHttpError, match="digest"):
            await genome.resolve_revision(BOT, "active")
    fixture.conn.close()


@pytest.mark.asyncio
async def test_real_session_snapshot_bounded_redacted_durable_and_not_revision_attributed(tmp_path):
    sid = "agent:main:session:one:user:local-user"
    at = datetime.now(UTC).isoformat()
    calls = []

    def handle(req):
        calls.append(req)
        assert req.url.params["page"] == "1" and "page_no" not in req.url.params
        assert req.url.params["stage"] == "online"
        if req.url.path.endswith("/messages"):
            return ok(
                {
                    "items": [
                        {
                            "session_id": sid,
                            "role": "user",
                            "content": "password=private-value contact a@example.org",
                            "gmt_create": at,
                            "metadata": {"private": "discard"},
                        }
                    ]
                }
            )
        return ok({"items": [{"session_id": sid, "gmt_modified": at, "gmt_create": at}], "total": 500})

    async with httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(handle)) as c:
        factory = HttpSessionExperience(BotHttp(c, headers), tmp_path / "sessions.db", stage="online")
        bound = factory.for_bot(BOT)
        episodes = await bound.sessions(days=7, limit=1)
        assert len(calls) == 2 and calls[0].url.params["page_size"] == "1"
        assert len(episodes) == 1 and episodes[0]["revision"] is None
        assert episodes[0]["history_complete"] is False
        encoded = json.dumps(episodes)
        assert "private-value" not in encoded and "example.org" not in encoded and "metadata" not in encoded
        assert factory.conn.execute("SELECT count(*) FROM session_snapshot").fetchone()[0] == 1
        assert await bound.sessions(days=7, limit=1) == episodes
        assert factory.conn.execute("SELECT count(*) FROM session_snapshot").fetchone()[0] == 1
        calls.clear()
        assert await bound.sessions(days=7, revision="historical") == [] and not calls
        factory.conn.execute("PRAGMA query_only=ON")
        with pytest.raises(Exception, match="readonly"):
            await bound.sessions(days=7)
        factory.close()


@pytest.mark.asyncio
async def test_cross_session_messages_fail_closed(tmp_path):
    at = datetime.now(UTC).isoformat()

    def handle(req):
        return (
            ok({"items": [{"session_id": "wrong"}]})
            if req.url.path.endswith("/messages")
            else ok({"items": [{"session_id": "right", "gmt_modified": at}]})
        )

    async with httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(handle)) as c:
        f = HttpSessionExperience(BotHttp(c, headers), tmp_path / "sessions.db", stage="online")
        with pytest.raises(PlatformHttpError, match="identity mismatch"):
            await f.for_bot(BOT).sessions(days=7)
        assert f.conn.execute("SELECT count(*) FROM session_snapshot").fetchone()[0] == 0
        f.close()


def test_host_and_kubernetes_execute_same_real_worker_entrypoint():
    assert (
        _default_command(None) == KubernetesJobWorker(None, namespace="simulator").command == WORKER_COMMAND
    )
    assert "--worker" not in WORKER_COMMAND


@pytest.mark.parametrize("credential", [{"Cookie": "not-a-delegation"}, PlatformCredential({}, "")])
@pytest.mark.asyncio
async def test_background_identity_cannot_fall_back_to_bot_owner(credential):
    calls = []

    async def resolve(bot):
        return credential

    async with httpx.AsyncClient(
        base_url="http://platform.test",
        transport=httpx.MockTransport(lambda req: calls.append(req) or ok({})),
    ) as client:
        with pytest.raises(PlatformHttpError, match="delegation is not configured"):
            await BotHttp(client, resolve).request(BOT, "GET", "/sessions")
    assert not calls


@pytest.mark.asyncio
async def test_genome_drift_reads_the_public_observation_endpoint():
    from avernet_evolution.plugins.platform_http import Drift

    paths = []

    def digest(n):
        return "sha256:" + n * 64

    def handle(req):
        paths.append(req.url.path)
        return ok({"drift": {"active": digest("1"), "observed": digest("2")}})

    async with httpx.AsyncClient(base_url="http://platform.test", transport=httpx.MockTransport(handle)) as c:
        drift = await HttpGenome(BotHttp(c, headers)).drift(BOT)
    assert drift == Drift(digest("1"), digest("2"))
    assert paths == [f"/openapi/v1/bots/{BOT.bot_id}/genome/drift"]


@pytest.mark.asyncio
async def test_genome_drift_absence_is_null_not_an_invented_baseline():
    async with httpx.AsyncClient(
        base_url="http://platform.test", transport=httpx.MockTransport(lambda r: ok({"drift": None}))
    ) as c:
        assert await HttpGenome(BotHttp(c, headers)).drift(BOT) is None


@pytest.mark.parametrize(
    "body", [[], {}, {"drift": {"active": "a"}}, {"drift": {"active": "a", "observed": 5}}]
)
@pytest.mark.asyncio
async def test_genome_drift_refuses_a_malformed_observation(body):
    async with httpx.AsyncClient(
        base_url="http://platform.test", transport=httpx.MockTransport(lambda r: ok(body))
    ) as c:
        with pytest.raises(PlatformHttpError, match="drift response"):
            await HttpGenome(BotHttp(c, headers)).drift(BOT)
