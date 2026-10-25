"""Live HTTP -> native checks -> SQL selection; only external catalog is doubled.

The unified runner supplies fixture_catalog.json at the existing local Plugin
API composition seam. No direct calls to business code or in-process ASGI app.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

pytestmark = pytest.mark.acceptance
OWNER = "bk_live_owner"
EDITOR = "bk_live_editor"


def sql(client, statement, **params):
    response = client.post(
        "/local/sql/execute", json={"sql": statement, "params": params}
    )
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


def cleanup(client):
    for table in ("ac_bot_knowledge_binding", "ac_bot_collaborator"):
        sql(
            client,
            f"DELETE FROM {table} WHERE bot_pk IN (SELECT id FROM ac_bots WHERE entity_id=:owner)",
            owner=OWNER,
        )
    sql(client, "DELETE FROM ac_bot_publish WHERE owner_id=:owner", owner=OWNER)
    sql(client, "DELETE FROM ac_bots WHERE entity_id=:owner", owner=OWNER)
    sql(client, "DELETE FROM ac_space_member WHERE space_id IN (3087001,3087002)")
    sql(client, "DELETE FROM ac_space WHERE id IN (3087001,3087002)")


@contextmanager
def seeded(base_url, *, personal=False, default=False):
    bot_id = (
        "default" if default else ("bk-live-personal" if personal else "bk-live-team")
    )
    space_id = 3087001 if personal else 3087002
    with httpx.Client(
        base_url=base_url, headers={"x-user-id": OWNER}, timeout=30, trust_env=False
    ) as client:
        cleanup(client)
        try:
            sql(
                client,
                "INSERT INTO ac_space (id,space_code,space_type,name,personal_owner_id,env,created_by,updated_by) VALUES (:id,:code,:type,'Knowledge live',:personal_owner,'dev',:owner,:owner)",
                id=space_id,
                code=f"knowledge-live-{space_id}",
                type="PERSONAL" if personal else "TEAM",
                personal_owner=OWNER if personal else None,
                owner=OWNER,
            )
            users = [OWNER] if personal else [OWNER, EDITOR]
            for user in users:
                sql(
                    client,
                    "INSERT INTO ac_space_member (space_id,user_id,role,status,env,created_by) VALUES (:space,:user,'MEMBER','ACTIVE','dev',:owner)",
                    space=space_id,
                    user=user,
                    owner=OWNER,
                )
            insert_bot(client, bot_id, space_id)
            sql(
                client,
                "INSERT INTO ac_bot_collaborator (bot_pk,bot_id,owner_id,user_id,role,operator_id,env,gmt_create,gmt_modified) VALUES ((SELECT id FROM ac_bots WHERE bot_id=:bot AND owner_id=:owner AND avernet_tenant='teamclaw'),:bot,:owner,:editor,'member',:owner,'dev',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                bot=bot_id,
                owner=OWNER,
                editor=EDITOR,
            )
            yield client, bot_id
        finally:
            cleanup(client)


def insert_bot(client, bot_id, space_id, tenant="teamclaw"):
    sql(
        client,
        """INSERT INTO ac_bots (
        bot_id,bot_name,entity_id,entity_type,creator_id,owner_id,engine_types,
        active_engine,status,is_delete,public,ext,env,bot_type,template_type,
        call_type,caller_config_revision,space_id,avernet_tenant,gmt_create,gmt_modified
    ) VALUES (:bot,'Knowledge live',:owner,'staff',:owner,:owner,'["openclaw"]',
        'openclaw','ACTIVE',0,'0','{}','dev','service','chat','owner',0,:space,:tenant,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
        bot=bot_id,
        owner=OWNER,
        space=space_id,
        tenant=tenant,
    )
    pk = sql(
        client,
        "SELECT id FROM ac_bots WHERE bot_id=:bot AND entity_id=:owner AND avernet_tenant=:tenant",
        bot=bot_id,
        owner=OWNER,
        tenant=tenant,
    )["rows"][0]["id"]
    sql(
        client,
        "INSERT INTO ac_bot_publish (source_bot_pk,source_bot_id,publish_bot_id,name,owner_id,status,version,env,permission_owner,last_pub_id,gmt_create,gmt_modified) VALUES (:pk,:bot,:bot,'Knowledge live',:owner,'draft',1,'dev',:owner,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
        pk=pk,
        bot=bot_id,
        owner=OWNER,
    )
    return pk


def put(client, bot, *, kb="kb", enabled=True, revision=0, actor=OWNER):
    return client.put(
        f"/api/bot-knowledge/{bot}/{kb}",
        headers={"x-user-id": actor},
        json={"owner_id": OWNER, "enabled": enabled, "expected_revision": revision},
    )


def listing(client, bot, *, actor=OWNER, **page):
    return client.get(
        f"/api/bot-knowledge/{bot}",
        headers={"x-user-id": actor},
        params={"owner_id": OWNER, **page},
    )


def error(response, code, status=403):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def test_personal_explicit_editor_current_selection_live(live_backend):
    with seeded(live_backend, personal=True) as (client, bot):
        # Only owner has a Space-member row; the explicit editor is still valid.
        assert listing(client, bot, actor=EDITOR).json()["items"] == []
        error(listing(client, bot, actor="bk_live_stranger"), "BOT_EDITOR_REQUIRED")
        first = put(client, bot, actor=EDITOR)
        assert first.status_code == 200, first.text
        assert first.json()["revision"] == 1
        assert put(client, bot, actor=EDITOR, revision=1).json() == first.json()
        error(put(client, bot), "REVISION_CONFLICT", 409)
        error(put(client, bot, kb="not-granted"), "KNOWLEDGE_NOT_GRANTED")
        assert put(client, bot, kb="second", actor=EDITOR).status_code == 200
        page = listing(client, bot, actor=EDITOR, limit=1).json()
        assert len(page["items"]) == 1 and page["next_cursor"] is not None
        assert (
            len(listing(client, bot, cursor=page["next_cursor"]).json()["items"]) == 1
        )
        assert (
            put(client, bot, actor=EDITOR, enabled=False, revision=1).json()["revision"]
            == 2
        )
        # No catalog grant exists for this key: local disable is still a no-op.
        absent = put(client, bot, actor=EDITOR, kb="not-granted", enabled=False)
        assert absent.status_code == 200 and absent.json()["revision"] == 0
        rows = sql(
            client,
            "SELECT kb_id,enabled,revision FROM ac_bot_knowledge_binding WHERE bot_pk=(SELECT id FROM ac_bots WHERE bot_id=:bot AND owner_id=:owner)",
            bot=bot,
            owner=OWNER,
        )["rows"]
        assert sorted((r["kb_id"], r["enabled"], r["revision"]) for r in rows) == [
            ("kb", 0, 2),
            ("second", 1, 1),
        ]


def test_team_departure_and_draft_lifecycle_live(live_backend):
    with seeded(live_backend) as (client, bot):
        assert put(client, bot, actor=EDITOR).status_code == 200
        sql(
            client,
            "UPDATE ac_space_member SET status='INACTIVE' WHERE space_id=3087002 AND user_id=:editor",
            editor=EDITOR,
        )
        error(listing(client, bot, actor=EDITOR), "SPACE_MEMBER_REQUIRED")
        error(
            put(client, bot, actor=EDITOR, enabled=False, revision=1),
            "SPACE_MEMBER_REQUIRED",
        )
        sql(
            client,
            "UPDATE ac_bot_publish SET status='success' WHERE owner_id=:owner",
            owner=OWNER,
        )
        error(put(client, bot, enabled=False, revision=1), "BOT_DRAFT_REQUIRED")
        sql(
            client,
            "UPDATE ac_bot_publish SET status='draft' WHERE owner_id=:owner",
            owner=OWNER,
        )
        assert put(client, bot, enabled=False, revision=1).json()["revision"] == 2


def test_default_cross_tenant_latest_and_orphan_fail_closed_live(live_backend):
    with seeded(live_backend, default=True) as (client, bot):
        sql(
            client,
            "UPDATE ac_bot_publish SET status='success' WHERE owner_id=:owner",
            owner=OWNER,
        )
        foreign_pk = insert_bot(client, bot, 3087002, tenant="external-b")
        sql(
            client,
            "UPDATE ac_bot_publish SET version=9 WHERE source_bot_pk=:pk",
            pk=foreign_pk,
        )
        error(put(client, bot), "BOT_DRAFT_REQUIRED")
        assert listing(client, bot).json()["items"] == []
        sql(
            client,
            "UPDATE ac_bot_publish SET status=CASE WHEN source_bot_pk=:pk THEN 'success' ELSE 'draft' END WHERE owner_id=:owner",
            pk=foreign_pk,
            owner=OWNER,
        )
        assert put(client, bot).status_code == 200
        # Tenant cannot be inferred from an orphan source PK. Do not select an
        # older known draft if that ambiguous successor exists.
        sql(
            client,
            "INSERT INTO ac_bot_publish (source_bot_pk,source_bot_id,publish_bot_id,name,owner_id,status,version,env,permission_owner,last_pub_id,gmt_create,gmt_modified) VALUES (999999999,'default','default','Orphan',:owner,'draft',10,'dev',:owner,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            owner=OWNER,
        )
        error(put(client, bot, enabled=False, revision=1), "BOT_DRAFT_REQUIRED")
