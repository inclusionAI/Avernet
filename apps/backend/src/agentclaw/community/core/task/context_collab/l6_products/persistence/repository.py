"""L6 product persistence and legacy combined-creation compatibility.

Primitive creation/read/append SQL is implemented in L4. This extension adds
ProductInstance installation and product listing; DI injects a separate, exact
L4 repository instance into the L4 service. Both use the same DatabasePlugin.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Protocol, Sequence, runtime_checkable

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g2_keep_reliable_live.reliability import (
    ensure_expected_version,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    AppendEventRecordRequest as AppendEventRecordRequest,
    ColletCreateCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.creation import (
    build_created_collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.models import (
    ContextCollabColletModel,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.repository import (
    ContextCollabRepository as L4ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.row_codec import (
    _collet_from_row as _primitive_collet_from_row,
    _dump_json,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.task_storage import (
    load_tasks,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_method_kind,
    creation_product_kind,
    decode_creation_facts,
)
from agentclaw.community.core.task.context_collab.l6_products.instance import (
    ProductInstance,
    decode_product_instance,
    instantiate_product,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextCreatedRecord as ContextCreatedRecord,
    ContextStatusRecord as ContextStatusRecord,
    CreateColletRecordRequest as CreateColletRecordRequest,
    EventAppendedRecord as EventAppendedRecord,
    InitialEventDraft as InitialEventDraft,
    ProductRecordDecoder,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.env_utils import get_current_env

_logger = logging.getLogger(__name__)


@runtime_checkable
class ContextCollabRepositoryProtocol(Protocol):
    """L6 composition storage and rich legacy result contract."""

    def create_collet(
        self, request: CreateColletRecordRequest
    ) -> ContextCreatedRecord: ...

    def create_l4_collet(
        self, command: ColletCreateCommand, actor_ref: PrincipalRef
    ) -> ContextCreatedRecord: ...

    def attach_product_instance(
        self,
        collab_context_id: str,
        *,
        expected_version: int,
        facts: dict,
        instance: ProductInstance,
    ) -> None:
        """CAS-install L6 selection on an untouched, method-independent Collet.

        Does not rewrite the original L4 creation Event or advance Work. Product
        selection is immutable; repeats/conflicting selections fail closed.
        """
        ...

    def append_event(
        self, collab_context_id: str, request: "AppendEventRecordRequest"
    ) -> "EventAppendedRecord": ...

    def get_current_status(
        self, collab_context_id: str
    ) -> "ContextStatusRecord | None": ...

    def list_collets_by_product(
        self, product_kind: str
    ) -> "list[tuple[Collet, dict, datetime]]": ...

    def count_message_events(
        self, collab_context_ids: "Sequence[str]"
    ) -> "dict[str, int]": ...


def _collet_from_row(row: ContextCollabColletModel) -> tuple[Collet, dict]:
    return _primitive_collet_from_row(row), decode_creation_facts(
        json.loads(row.creation_facts)
    )


class ContextCollabRepository:
    @inject
    def __init__(self, db: DatabasePlugin, product_catalog: ProductCatalog) -> None:
        self._db = db
        self._product_catalog = product_catalog
        self._primitives = L4ContextCollabRepository(
            db, ProductRecordDecoder(product_catalog)
        )

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    def create_l4_collet(
        self, command: ColletCreateCommand, actor_ref: PrincipalRef
    ) -> ContextCreatedRecord:
        """Legacy rich result; actual creation is implemented entirely in L4."""
        created = self._primitives.create_l4_collet(command, actor_ref)
        return ContextCreatedRecord(
            collet=created.collet,
            task=created.task,
            initial_event=created.initial_event,
            creation_facts=dict(created.initial_event.payload),
        )

    def get_current_status(self, collab_context_id: str) -> ContextStatusRecord | None:
        return self._primitives.get_current_status(collab_context_id)

    def count_message_events(self, collab_context_ids: Sequence[str]) -> dict[str, int]:
        return self._primitives.count_message_events(collab_context_ids)

    def create_collet(self, request: CreateColletRecordRequest) -> ContextCreatedRecord:
        """同一事务创建 Collet、独立 Task 及初始事件。

        创建不设幂等键(spec 作者裁决):重复调用即重复创建,自然防重
        由调用方保证。
        """
        facts = decode_creation_facts(
            request.creation_facts
            or {
                "objective": request.objective,
                "resource_refs": [ref.to_dict() for ref in request.resource_refs],
                "method_kind": request.method_kind,
                "executor_ref": request.executor_ref.to_dict()
                if request.executor_ref
                else None,
                "product_kind": request.product_kind,
                "workflow_kind": request.workflow_kind,
                "product_profile_ref": (
                    {
                        "profile_id": request.product_profile_ref.profile_id,
                        "version": request.product_profile_ref.version,
                    }
                    if request.product_profile_ref is not None
                    else None
                ),
            }
        )
        record = build_created_collet(
            ColletCreateCommand(
                to_space=request.space,
                initiator_ref=request.principal_ref,
                objective=request.objective,
                executor_refs=request.executor_refs
                or (
                    (request.executor_ref,) if request.executor_ref is not None else ()
                ),
            ),
            request.initial_event,
            request.completion_conditions,
        )
        instance = (
            instantiate_product(
                facts=facts,
                collet_id=record.collet.collet_id,
                event_id=record.initial_event.event_id,
                principal_ref=request.principal_ref,
                executor_ref=request.executor_ref,
                catalog=self._product_catalog,
            )
            if facts.get("product_profile_ref") is not None
            else None
        )
        stored_facts = {
            **facts,
            **(
                {"product_instance": instance.to_dict()} if instance is not None else {}
            ),
        }
        self._primitives.persist_created(record, _dump_json(stored_facts))
        return ContextCreatedRecord(
            collet=record.collet,
            creation_facts=stored_facts,
            task=record.task,
            initial_event=record.initial_event,
            product_instance=instance,
        )

    def attach_product_instance(
        self,
        collab_context_id: str,
        *,
        expected_version: int,
        facts: dict,
        instance: ProductInstance,
    ) -> None:
        with self._transaction() as db:
            row = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.collab_context_id == collab_context_id,
                    ContextCollabColletModel.env == get_current_env(),
                )
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ContextCollabNotFoundError(
                    f"context {collab_context_id!r} not found"
                )
            ensure_expected_version(expected_version, int(row.context_version))
            _, previous = _collet_from_row(row)
            task, _ = load_tasks(db, row)
            if (
                expected_version != 1
                or task is None
                or task.version != 1
                or creation_method_kind(previous) is not None
            ):
                raise ContextCollabValidationError(
                    "product instantiation requires an untouched L4 Collet"
                )
            stored = decode_creation_facts(
                {**facts, "product_instance": instance.to_dict()}
            )
            if (
                instance.collet_ref != collab_context_id
                or instance.creation_event_ref != task.created_event_ref
                or stored["objective"] != task.objective
                or stored.get("executor_ref")
                != (task.executor_ref.to_dict() if task.executor_ref else None)
                or len(task.executor_refs) > 1
            ):
                raise ContextCollabValidationError(
                    "product selection does not match L4 creation"
                )
            decoded = decode_product_instance(
                stored,
                collet_id=collab_context_id,
                event_id=task.created_event_ref,
                principal_ref=task.principal_ref,
                executor_ref=task.executor_ref,
                catalog=self._product_catalog,
            )
            if decoded != instance:
                raise ContextCollabValidationError(
                    "product selection must include its pinned instance"
                )
            updated = (
                db.query(ContextCollabColletModel)
                .filter(
                    ContextCollabColletModel.id == row.id,
                    ContextCollabColletModel.context_version == expected_version,
                    ContextCollabColletModel.creation_facts == row.creation_facts,
                )
                .update(
                    {"creation_facts": _dump_json(stored)}, synchronize_session=False
                )
            )
            if updated != 1:
                raise ContextCollabVersionConflict("product selection CAS failed")
            db.flush()

    def append_event(
        self, collab_context_id: str, request: AppendEventRecordRequest
    ) -> EventAppendedRecord:
        saved = self._primitives.append_event(collab_context_id, request)
        return EventAppendedRecord(
            event=saved.event,
            collet=saved.collet,
            task=saved.task,
            creation_facts=decode_creation_facts(json.loads(saved.creation_facts_json)),
        )

    def list_collets_by_product(
        self, product_kind: str
    ) -> list[tuple[Collet, dict, datetime]]:
        """按 product profile 列 Collet(产品列表读,spec bbs-public-discussion §4)。

        域类型直出,零发明 record:元组三元素 = 聚合根、创建参数
        (过滤源 + 过渡期 title 伴随)、``gmt_create``。过滤在 Python 侧
        解 ``creation_facts`` JSON 后进行(§6-E 裁决:表小、方言差异大);
        按 ``gmt_create`` 倒序。**列表容错**:严格解码(``_collet_from_row``
        抛 ``ContextCollabValidationError``)的外部部分行跳过并告警——
        单点 ``get_current_status`` 维持方案 i 响亮失败,不动。
        """
        with self._db.orm_session() as db:
            rows = (
                db.query(ContextCollabColletModel)
                .filter(ContextCollabColletModel.env == get_current_env())
                .order_by(ContextCollabColletModel.gmt_create.desc())
                .all()
            )
            listed: list[tuple[Collet, dict, datetime]] = []
            for row in rows:
                try:
                    collet, facts = _collet_from_row(row)
                except ContextCollabValidationError as error:
                    _logger.warning(
                        "skip undecodable collab context row %s in product listing: %s",
                        row.collab_context_id,
                        error,
                    )
                    continue
                if creation_product_kind(facts) != product_kind:
                    continue
                listed.append((collet, facts, row.gmt_create))
            return listed
