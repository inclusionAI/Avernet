"""Knowledge selection effects inside the caller's publication transaction.

No remote I/O, independent commit, Bot.ext writes or knowledge-content snapshots. Bot
locking is shared with the binding writer (including SQLite's write reservation).
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from agentclaw.community.core.bot_knowledge.repository.models import (
    BotKnowledgeBindingModel as Binding,
)
from agentclaw.community.core.bot_knowledge.repository.online_selection import (
    lock_summary, write_summary,
)
from agentclaw.community.core.service_bot.repository.config_artifact_offload import (
    ARTIFACT_KEY,
    ARTIFACT_OSS_MARKER,
)
from agentclaw.community.core.service_bot.repository.models import (
    BotPublishModel as Publish,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationKind,
    PublishOperationState,
    PublishStatus,
)
from agentclaw.community.core.service_bot.repository.models import (
    PublishOperationModel as Operation,
)
from agentclaw.community.kernel.knowledge import (
    KnowledgeError,
    require_knowledge_id,
)
from agentclaw.community.plugin_api.models import BotCommonConfig, BotModel
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.env_utils import get_current_env
from sqlalchemy import bindparam, func, update

SNAPSHOT_KEY = "knowledge_selection_snapshot"
RESULT_KEY = "knowledge_selection_result"
_RESTORE_KEY = "knowledge_selection_restore"
_MAX_SELECTION = 100
_MAX_EXT_BYTES = 65535


def _locked_source(session, publish):
    source = _locked_native_source(session, publish)
    if source.entity_id != publish.owner_id:
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    return source


def _locked_native_source(session, publish):
    """Lock the native PK address; legacy Owner metadata is not its identity."""
    source = (
        session.query(BotModel)
        .filter(
            BotModel.id == publish.source_bot_pk,
            BotModel.bot_id == publish.source_bot_id,
            BotModel.env == publish.env,
            BotModel.bot_type == "service",
            BotModel.is_delete == 0,
        )
        .with_for_update()
        .one_or_none()
    )
    if source is None or publish.env != get_current_env():
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    # Same Bot row lock as binding writers, without a second address-range seek.
    # SQLite ignores FOR UPDATE, so reserve its writer before refreshing scope.
    session.execute(update(BotModel).where(BotModel.id == source.id).values(id=source.id))
    session.refresh(source, with_for_update=True)
    session.refresh(publish, with_for_update=True)
    if (
        source.id != publish.source_bot_pk
        or source.bot_id != publish.source_bot_id
        or source.env != publish.env
        or source.bot_type != "service"
        or source.is_delete
    ):
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    return source


def _bindings(session, bot):
    return session.query(Binding).filter(
        Binding.bot_pk == bot.id,
        Binding.env == bot.env,
        Binding.space_id == bot.space_id,
        Binding.avernet_tenant == bot.avernet_tenant,
    )


def _selected_bindings(session, bot, ids):
    """Lock the bounded KB rows through the existing unique-key prefix.

    Space and enabled are validated on the returned locked rows, not in the
    seek predicate, so the plan stays on (bot_pk, env, tenant, kb_id) for
    up to 100 IDs.
    """
    if not ids:
        return []
    return session.query(Binding).filter(
        Binding.bot_pk == bot.id, Binding.env == bot.env,
        Binding.avernet_tenant == bot.avernet_tenant,
        Binding.kb_id.in_(sorted(ids)),
    ).populate_existing().with_for_update().all()


def _write_online_rows(session, bot, rows, value, space_id):
    """One bounded executemany of PK equality UPDATEs, not planner-dependent IN.

    All native scope predicates (including tenant) remain explicit in this Core
    batch; no unguarded connection or independent Session is used.
    """
    changed = [r for r in rows if r.space_id == space_id and r.online_enabled != value]
    if not changed:
        return
    table = Binding.__table__
    statement = table.update().where(
        table.c.id == bindparam("_binding_pk"),
        table.c.bot_pk == bot.id, table.c.env == bot.env,
        table.c.avernet_tenant == bot.avernet_tenant, table.c.space_id == space_id,
        table.c.online_enabled.is_(not value),
    ).values(online_enabled=value)
    session.execute(statement, [{"_binding_pk": r.id} for r in changed])


def _ext(row):
    return json.loads(row.ext) if row.ext else {}


def encode_ext(ext):
    # Match offloader.prepare's persisted ordering without resolving or dropping
    # an existing OSS marker. Otherwise the very next exact ext CAS would miss.
    artifact_keys = (ARTIFACT_KEY, ARTIFACT_OSS_MARKER)
    canonical = {key: value for key, value in ext.items() if key not in artifact_keys}
    for key in artifact_keys:
        if key in ext:
            canonical[key] = ext[key]
    encoded = json.dumps(canonical, ensure_ascii=False)
    validate_encoded_ext(encoded)
    return encoded


def validate_encoded_ext(encoded):
    if encoded is None or len(encoded.encode("utf-8")) <= _MAX_EXT_BYTES:
        return
    ext = json.loads(encoded)
    if any(key in ext for key in (SNAPSHOT_KEY, RESULT_KEY, _RESTORE_KEY)):
        raise KnowledgeError("KNOWLEDGE_PUBLISH_EXT_TOO_LARGE")


@contextmanager
def publication_scope(session, publish_id):
    """Route durable work from trusted, immutable ext; never bypass tenant guards."""
    row = session.query(Publish).filter(Publish.id == publish_id).one_or_none()
    snapshot = _ext(row).get(SNAPSHOT_KEY) if row is not None else None
    tenant = snapshot.get("avernet_tenant") if isinstance(snapshot, dict) else None
    if (
        isinstance(tenant, str)
        and 0 < len(tenant) <= 64
        and snapshot.get("publish_id") == row.id
        and snapshot.get("source_bot_pk") == row.source_bot_pk
        and snapshot.get("source_entity_id") == row.owner_id
        and snapshot.get("env") == row.env
    ):
        with avernet_tenant_scope(tenant):
            yield
    else:
        yield


def _unknown(exc):
    return {"status": "unknown", "reason": str(exc)}


def _superseded_outcome(code, workflow_id, result_owner):
    """No knowledge effects; the native ledger still completes with a record."""
    outcome = _unknown(KnowledgeError(code))
    outcome["deployment_id"] = workflow_id if type(workflow_id) is int else 0
    outcome["owner_id"] = result_owner
    return outcome


def _snapshot_scope(publish, source):
    return {
        "schema_version": 1,
        "source_bot_pk": source.id,
        "owner_id": source.owner_id,
        "source_entity_id": source.entity_id,
        "space_id": source.space_id,
        "env": source.env,
        "avernet_tenant": source.avernet_tenant,
        "publish_id": publish.id,
    }


def validate_snapshot(publish, source):
    snapshot = _ext(publish).get(SNAPSHOT_KEY)
    if snapshot is None:
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_MISSING")
    expected = _snapshot_scope(publish, source)
    if not isinstance(snapshot, dict) or any(
        type(snapshot.get(k)) is not type(v) or snapshot.get(k) != v
        for k, v in expected.items()
    ):
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_SCOPE_MISMATCH")
    ids = snapshot.get("enabled_kb_ids")
    generation = snapshot.get("generation")
    if (
        not isinstance(generation, str)
        or len(generation) != 32
        or not isinstance(ids, list)
        or len(ids) > _MAX_SELECTION
        or any(not isinstance(kb, str) for kb in ids)
        or len(set(ids)) != len(ids)
        or (source.space_id is None and ids)
    ):
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_INVALID")
    try:
        for kb in ids:
            require_knowledge_id(kb, "kb_id")
        int(generation, 16)
    except ValueError as exc:
        raise KnowledgeError("KNOWLEDGE_SNAPSHOT_INVALID") from exc
    return snapshot


def prepare_build(session, publish_id, generation, incoming_ext=None):
    publish = session.query(Publish).filter(Publish.id == publish_id).one_or_none()
    if publish is None or publish.status != PublishStatus.DRAFT:
        return False
    ext = _ext(publish)
    source = None
    try:
        source = _locked_source(session, publish)
        if publish.status != PublishStatus.DRAFT:
            return False
        ext = (_ext(publish) if incoming_ext is None else
               merge_protected_ext(_ext(publish), incoming_ext))
        ids = [
            r[0]
            for r in _bindings(session, source)
            .with_entities(Binding.kb_id)
            .filter(Binding.enabled.is_(True))
            .order_by(Binding.kb_id)
            .limit(_MAX_SELECTION + 1)
            .with_for_update()
            .all()
        ]
        if len(ids) > _MAX_SELECTION:
            raise KnowledgeError("KNOWLEDGE_LIMIT_EXCEEDED")
        ext[SNAPSHOT_KEY] = {
            **_snapshot_scope(publish, source),
            "generation": generation,
            "enabled_kb_ids": ids,
        }
    except KnowledgeError as exc:
        # Not an empty selection, and never recaptured from a later draft.
        # SQL/storage failures are deliberately NOT caught.
        session.refresh(publish, with_for_update=True)
        if publish.status != PublishStatus.DRAFT:
            return False
        ext = (_ext(publish) if incoming_ext is None else
               merge_protected_ext(_ext(publish), incoming_ext))
        ext[SNAPSHOT_KEY] = {
            **(_snapshot_scope(publish, source) if source is not None else {}),
            "generation": generation,
            **_unknown(exc),
        }
    return publish, ext


def commit_build(session, publish, encoded):
    publish.ext = encoded
    publish.status = PublishStatus.BUILDING
    publish.gmt_modified = func.now()
    session.flush()


def protected_ext(session, publish_id, incoming):
    """A legacy/background ext writer cannot erase or replace a frozen selection."""
    row = (
        session.query(Publish)
        .filter(Publish.id == publish_id)
        .with_for_update()
        .one_or_none()
    )
    return merge_protected_ext(_ext(row) if row is not None else {}, incoming)


def merge_protected_ext(stored, incoming):
    result = copy.deepcopy(incoming)
    for key in (SNAPSHOT_KEY, RESULT_KEY, _RESTORE_KEY):
        if key in result and result[key] != stored.get(key):
            raise KnowledgeError("KNOWLEDGE_SNAPSHOT_READ_ONLY")
        if key in stored:
            result[key] = copy.deepcopy(stored[key])
    return result


@dataclass(frozen=True)
class OnlineChange:
    publish: Publish
    source: BotModel | None
    snapshot: dict | None
    outcome: dict
    summary_row: BotCommonConfig | None
    previous: dict | None
    superseded: bool = False


def prepare_online(session, publish_id, expected_json, observed_workflow_id=None):
    """Lock Bot before publication CAS; reject a stale outcome without effects."""
    publish = session.query(Publish).filter(Publish.id == publish_id).one_or_none()
    if (
        publish is None
        or publish.status != PublishStatus.ONLINE_PUB
        or publish.ext != expected_json
    ):
        return None
    source = None
    snapshot = None
    outcome = {"status": "applied"}
    try:
        source = _locked_source(session, publish)
        if publish.status != PublishStatus.ONLINE_PUB or publish.ext != expected_json:
            return None
        snapshot = validate_snapshot(publish, source)
        ids = snapshot["enabled_kb_ids"]
        present = {r.kb_id for r in _selected_bindings(session, source, ids)
                   if r.space_id == source.space_id}
        if not set(ids).issubset(present):
            raise KnowledgeError("KNOWLEDGE_SNAPSHOT_BINDING_MISSING")
    except KnowledgeError as exc:
        snapshot = None
        outcome = _unknown(exc)
    # Even unknown history must take the publication lock before CAS. There is
    # no lock on a foreign/missing Bot and no substitution by same-named Bot.
    session.refresh(publish, with_for_update=True)
    if publish.status != PublishStatus.ONLINE_PUB or publish.ext != expected_json:
        return None
    workflow_id = observed_workflow_id
    # Publication.owner_id is the immutable entity address, NOT current Owner.
    # Scope the effect to the locked current Owner; snapshot validation already
    # prevents cross-Owner grants, and newer observed results fence late work.
    result_owner = source.owner_id if source is not None else None
    if workflow_id is not None and type(workflow_id) is not int:
        # An unorderable observed workflow cannot fence against the recorded
        # watermark. Fail closed: complete the native ledger with an explicit
        # unknown outcome; the recorded current result stays untouched.
        return OnlineChange(
            publish, None, None,
            _superseded_outcome(
                "KNOWLEDGE_OBSERVED_WORKFLOW_INVALID", workflow_id, result_owner,
            ),
            None, None, superseded=True,
        )
    summary_row, previous = lock_summary(session, publish)
    if previous is not None and type(workflow_id) is int and previous["deployment_id"] > workflow_id:
        # A newer observed deployment already recorded the current result and
        # owns the materialized selection. Complete the native ledger with an
        # explicit late/unknown outcome instead of stalling in ONLINE_PUB;
        # the newer summary and its flags stay authoritative.
        return OnlineChange(
            publish, None, None,
            _superseded_outcome(
                "KNOWLEDGE_SUPERSEDED_BY_NEWER_DEPLOYMENT", workflow_id, result_owner,
            ),
            None, None, superseded=True,
        )
    if summary_row is not None and previous is None:
        snapshot = None
        outcome = _unknown(KnowledgeError("KNOWLEDGE_CURRENT_SUMMARY_INVALID"))
    outcome["deployment_id"] = workflow_id if type(workflow_id) is int else 0
    outcome["owner_id"] = result_owner
    return OnlineChange(publish, source, snapshot, outcome, summary_row, previous)


def replace_online(session, change):
    """Point updates of bounded IDs + current result in the winning CAS Session."""
    if change.superseded:
        # A newer observed deployment owns the current result and the
        # materialized selection. The late or unorderable completion recorded
        # its protected ext outcome through the CAS; touching flags or the
        # summary here would revert the winning deployment.
        return
    source, snapshot = change.source, change.snapshot
    ids = set(snapshot["enabled_kb_ids"]) if snapshot is not None else set()
    materialized = change.previous.get("materialized") if change.previous else None
    if source is not None:
        # The recorded previous physical scope survives UNKNOWN and space moves.
        # Never clear a different tenant based on untrusted historical metadata.
        if materialized is not None and materialized["avernet_tenant"] == source.avernet_tenant:
            old_ids = set(materialized["kb_ids"])
            removed = old_ids - ids if materialized["space_id"] == source.space_id else old_ids
            if removed:
                _write_online_rows(
                    session, source, _selected_bindings(session, source, removed),
                    False, materialized["space_id"],
                )
        if ids:
            _write_online_rows(
                session, source, _selected_bindings(session, source, ids),
                True, source.space_id,
            )
        materialized = dict(
            avernet_tenant=source.avernet_tenant, space_id=source.space_id,
            kb_ids=sorted(ids),
        )
    write_summary(
        session, change.summary_row, publish=change.publish, source=source,
        outcome=change.outcome, ids=ids, materialized=materialized,
    )


def _locked_restore_operation(
    session,
    draft,
    *,
    source_publish_id,
    operation_id,
    expected_attempt,
):
    operation = (
        session.query(Operation)
        .filter(Operation.id == operation_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        operation is None
        or operation.publish_id != draft.id
        or operation.operation_kind != str(PublishOperationKind.DRAFT_RESTORE)
        or operation.stage != "draft"
        or operation.env != draft.env
        or type(expected_attempt) is not int
        or expected_attempt <= 0
        or operation.attempt != expected_attempt
    ):
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_MISMATCH")
    params = json.loads(operation.params) if operation.params else {}
    if (
        not isinstance(params, dict)
        or type(params.get("source_publish_id")) is not int
        or params["source_publish_id"] != source_publish_id
        or type(params.get("source_version")) is not int
        or params["source_version"] <= 0
    ):
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_MISMATCH")
    # The ledger, not a marker left by a previous partial implementation, is
    # authoritative. Completed delivery must never replay the selection write.
    if operation.state == PublishOperationState.COMPLETED:
        return operation, params
    if operation.state not in (
        PublishOperationState.PENDING,
        PublishOperationState.ID_RECORDED,
    ):
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_TERMINAL")
    latest = (
        session.query(Operation)
        .filter(
            Operation.publish_id == draft.id,
            Operation.operation_kind == str(PublishOperationKind.DRAFT_RESTORE),
            Operation.stage == "draft",
            Operation.env == draft.env,
        )
        .order_by(Operation.attempt.desc(), Operation.id.desc())
        .with_for_update()
        .first()
    )
    if latest.id != operation.id:
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_SUPERSEDED")
    return operation, params


def _require_restore_deadline(params):
    raw = params.get("deadline_at")
    try:
        if not isinstance(raw, str) or not raw:
            raise ValueError("deadline_at is missing")
        deadline = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise KnowledgeError("KNOWLEDGE_RESTORE_DEADLINE_INVALID") from exc
    if datetime.now(tz=deadline.tzinfo) >= deadline:
        raise KnowledgeError("KNOWLEDGE_RESTORE_OPERATION_EXPIRED")


def restore_draft(
    session,
    *,
    draft_publish_id,
    source_publish_id,
    operation_id,
    expected_attempt: int,
    final_result: dict,
):
    """Commit the selection and native operation completion in one transaction."""
    draft = session.query(Publish).filter(Publish.id == draft_publish_id).one_or_none()
    if draft is None:
        return False
    source = _locked_native_source(session, draft)
    operation, params = _locked_restore_operation(
        session,
        draft,
        source_publish_id=source_publish_id,
        operation_id=operation_id,
        expected_attempt=expected_attempt,
    )
    if operation.state == PublishOperationState.COMPLETED:
        return True
    latest = (
        session.query(Publish)
        .filter(
            Publish.source_bot_pk == source.id,
            Publish.env == source.env,
        )
        .order_by(Publish.version.desc(), Publish.id.desc())
        .with_for_update()
        .first()
    )
    if draft.status != PublishStatus.DRAFT or latest.id != draft.id:
        raise KnowledgeError("BOT_DRAFT_REQUIRED", 403)
    previous = (
        session.query(Publish)
        .filter(
            Publish.id == source_publish_id,
            Publish.source_bot_pk == source.id,
            Publish.env == source.env,
        )
        .with_for_update()
        .one_or_none()
    )
    if (
        previous is None
        or draft.last_pub_id != previous.id
        or previous.version != params["source_version"]
    ):
        raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
    outcome = {"status": "applied"}
    bindings = []
    ids = set()
    try:
        # Native restoration accepts legacy Owner semantics, but knowledge may
        # only be applied when both publication rows identify the live entity.
        if draft.owner_id != source.entity_id or previous.owner_id != source.entity_id:
            raise KnowledgeError("KNOWLEDGE_PUBLISH_SOURCE_MISMATCH")
        snapshot = validate_snapshot(previous, source)
        ids = set(snapshot["enabled_kb_ids"])
        enabled = (
            _bindings(session, source)
            .filter(Binding.enabled.is_(True))
            .with_for_update()
            .limit(_MAX_SELECTION + 1)
            .all()
        )
        if len(enabled) > _MAX_SELECTION:
            raise KnowledgeError("KNOWLEDGE_LIMIT_EXCEEDED")
        selected = [row for row in _selected_bindings(session, source, ids)
                    if row.space_id == source.space_id]
        bindings = list({row.id: row for row in enabled + selected}.values())
        if not ids.issubset({row.kb_id for row in bindings}):
            raise KnowledgeError("KNOWLEDGE_SNAPSHOT_BINDING_MISSING")
    except KnowledgeError as exc:
        bindings = []  # preserve working draft on unknown history
        outcome = _unknown(exc)
    # Check after every potentially blocking lock read, just before any writes.
    _require_restore_deadline(params)
    result_json = json.dumps(final_result, ensure_ascii=False)
    for row in bindings:
        value = row.kb_id in ids
        if row.enabled != value:
            row.enabled = value
            row.revision += 1
            row.updated_by = "publish_restore"
    ext = _ext(draft)
    ext[_RESTORE_KEY] = {
        "operation_id": operation_id,
        "source_publish_id": source_publish_id,
    }
    if outcome["status"] == "unknown":
        ext[_RESTORE_KEY]["knowledge"] = outcome
    draft.ext = encode_ext(ext)
    draft.gmt_modified = func.now()
    operation.result = result_json
    operation.state = PublishOperationState.COMPLETED.value
    operation.gmt_modified = func.now()
    session.flush()
    return True
