"""SQLAlchemy implementation of Todo template persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from injector import inject
from sqlalchemy import asc, desc

from agentclaw.community.core.repository.implementations.task.todos.template_models import (
    TodoTemplateModel,
    TodoTemplateVersionModel,
)
from agentclaw.community.core.repository.protocols.task.todo_templates import (
    TodoTemplateRepositoryProtocol,
)
from agentclaw.community.core.task.todos.errors import (
    TodoTemplateNotFoundError,
    TodoTemplateStateConflictError,
)
from agentclaw.community.core.task.todos.template_codec import (
    TodoTemplateDefinitionCodec,
)
from agentclaw.community.core.task.todos.template_management_models import (
    TodoTemplateDetail,
    TodoTemplatePage,
    TodoTemplateRecord,
    TodoTemplateStatus,
    TodoTemplateVersionRecord,
    TodoTemplateVersionStatus,
)
from agentclaw.community.core.task.todos.template_models import TodoTemplateDefinition
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


class TodoTemplateRepository(TodoTemplateRepositoryProtocol):
    """Persist Todo templates in the current tenant and environment."""

    @inject
    def __init__(
        self,
        db: DatabasePlugin,
        codec: TodoTemplateDefinitionCodec | None = None,
    ) -> None:
        self._db = db
        self._codec = codec or TodoTemplateDefinitionCodec()

    def _transaction(self):
        transactional = getattr(self._db, "transactional_orm_session", None)
        return transactional() if transactional is not None else self._db.orm_session()

    @staticmethod
    def _scope(query, model):
        return query.filter(
            model.env == get_current_env(),
            model.avernet_tenant == get_current_avernet_tenant(),
        )

    @classmethod
    def _template_query(cls, db, *, template_id: str):
        return cls._scope(db.query(TodoTemplateModel), TodoTemplateModel).filter(
            TodoTemplateModel.template_id == template_id
        )

    @classmethod
    def _require_template(cls, db, *, template_id: str, lock: bool = False):
        query = cls._template_query(db, template_id=template_id)
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise TodoTemplateNotFoundError(f"Todo template {template_id} not found")
        return row

    @classmethod
    def _version_query(cls, db, *, template_id: str, template_version_id: str):
        return cls._scope(
            db.query(TodoTemplateVersionModel), TodoTemplateVersionModel
        ).filter(
            TodoTemplateVersionModel.template_id == template_id,
            TodoTemplateVersionModel.template_version_id == template_version_id,
        )

    @classmethod
    def _require_version(
        cls,
        db,
        *,
        template_id: str,
        template_version_id: str,
        lock: bool = False,
    ):
        query = cls._version_query(
            db,
            template_id=template_id,
            template_version_id=template_version_id,
        )
        row = (query.with_for_update() if lock else query).one_or_none()
        if row is None:
            raise TodoTemplateNotFoundError(
                f"Todo template version {template_version_id} not found"
            )
        return row

    def create_template(
        self,
        *,
        space_id: int,
        definition: TodoTemplateDefinition,
        created_by: str,
    ) -> TodoTemplateDetail:
        encoded = self._codec.encode(definition)
        template_id = f"todo_tpl_{uuid.uuid4().hex}"
        version_id = f"todo_tplv_{uuid.uuid4().hex}"
        with self._transaction() as db:
            template = TodoTemplateModel(
                template_id=template_id,
                space_id=space_id,
                name=definition.name,
                description=definition.description,
                status=TodoTemplateStatus.ACTIVE.value,
                published_version_id=None,
                created_by=created_by,
                updated_by=created_by,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            version = TodoTemplateVersionModel(
                template_version_id=version_id,
                template_id=template_id,
                draft_template_id=template_id,
                version_no=1,
                status=TodoTemplateVersionStatus.DRAFT.value,
                schema_version=definition.schema_version,
                definition_json=encoded.definition_json,
                definition_hash=encoded.definition_hash,
                created_by=created_by,
                published_by=None,
                published_at=None,
                product_profile_id=None,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(template)
            db.add(version)
            db.flush()
            db.refresh(template)
            db.refresh(version)
            return TodoTemplateDetail(template.to_record(), (version.to_record(),))

    def get_template(self, *, template_id: str) -> TodoTemplateRecord:
        with self._db.orm_session() as db:
            return self._require_template(db, template_id=template_id).to_record()

    def list_templates(
        self,
        *,
        space_id: int,
        status: TodoTemplateStatus | None,
        page: int,
        page_size: int,
    ) -> TodoTemplatePage:
        with self._db.orm_session() as db:
            query = self._scope(db.query(TodoTemplateModel), TodoTemplateModel).filter(
                TodoTemplateModel.space_id == space_id
            )
            if status is not None:
                query = query.filter(TodoTemplateModel.status == status.value)
            total = query.count()
            rows = (
                query.order_by(
                    desc(TodoTemplateModel.gmt_modified), desc(TodoTemplateModel.id)
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return TodoTemplatePage(
                total=total,
                items=tuple(row.to_record() for row in rows),
            )

    def list_versions(
        self, *, template_id: str
    ) -> tuple[TodoTemplateVersionRecord, ...]:
        with self._db.orm_session() as db:
            self._require_template(db, template_id=template_id)
            rows = (
                self._scope(
                    db.query(TodoTemplateVersionModel), TodoTemplateVersionModel
                )
                .filter(TodoTemplateVersionModel.template_id == template_id)
                .order_by(asc(TodoTemplateVersionModel.version_no))
                .all()
            )
            return tuple(row.to_record() for row in rows)

    def get_version(
        self, *, template_id: str, template_version_id: str
    ) -> TodoTemplateVersionRecord:
        with self._db.orm_session() as db:
            self._require_template(db, template_id=template_id)
            return self._require_version(
                db,
                template_id=template_id,
                template_version_id=template_version_id,
            ).to_record()

    def create_draft(
        self, *, template_id: str, created_by: str
    ) -> TodoTemplateVersionRecord:
        with self._transaction() as db:
            template = self._require_template(db, template_id=template_id, lock=True)
            if template.status == TodoTemplateStatus.ARCHIVED.value:
                raise TodoTemplateStateConflictError(
                    "archived Todo template cannot create a draft"
                )
            draft = (
                self._scope(
                    db.query(TodoTemplateVersionModel), TodoTemplateVersionModel
                )
                .filter(
                    TodoTemplateVersionModel.template_id == template_id,
                    TodoTemplateVersionModel.status
                    == TodoTemplateVersionStatus.DRAFT.value,
                )
                .with_for_update()
                .one_or_none()
            )
            if draft is not None:
                raise TodoTemplateStateConflictError(
                    "Todo template draft already exists"
                )
            latest = (
                self._scope(
                    db.query(TodoTemplateVersionModel), TodoTemplateVersionModel
                )
                .filter(TodoTemplateVersionModel.template_id == template_id)
                .order_by(desc(TodoTemplateVersionModel.version_no))
                .with_for_update()
                .first()
            )
            if latest is None:
                raise TodoTemplateStateConflictError(
                    "Todo template has no source version"
                )
            draft = TodoTemplateVersionModel(
                template_version_id=f"todo_tplv_{uuid.uuid4().hex}",
                template_id=template_id,
                draft_template_id=template_id,
                version_no=latest.version_no + 1,
                status=TodoTemplateVersionStatus.DRAFT.value,
                schema_version=latest.schema_version,
                definition_json=latest.definition_json,
                definition_hash=latest.definition_hash,
                created_by=created_by,
                published_by=None,
                published_at=None,
                product_profile_id=None,
                env=get_current_env(),
                avernet_tenant=get_current_avernet_tenant(),
            )
            db.add(draft)
            db.flush()
            db.refresh(draft)
            return draft.to_record()

    def update_draft(
        self,
        *,
        template_id: str,
        template_version_id: str,
        definition: TodoTemplateDefinition,
        updated_by: str,
    ) -> TodoTemplateVersionRecord:
        encoded = self._codec.encode(definition)
        with self._transaction() as db:
            template = self._require_template(db, template_id=template_id, lock=True)
            version = self._require_version(
                db,
                template_id=template_id,
                template_version_id=template_version_id,
                lock=True,
            )
            if version.status != TodoTemplateVersionStatus.DRAFT.value:
                raise TodoTemplateStateConflictError(
                    "published Todo template version is immutable"
                )
            version.schema_version = definition.schema_version
            version.definition_json = encoded.definition_json
            version.definition_hash = encoded.definition_hash
            template.name = definition.name
            template.description = definition.description
            template.updated_by = updated_by
            db.flush()
            db.refresh(version)
            return version.to_record()

    def publish_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        published_by: str,
    ) -> TodoTemplateVersionRecord:
        with self._transaction() as db:
            template = self._require_template(db, template_id=template_id, lock=True)
            version = self._require_version(
                db,
                template_id=template_id,
                template_version_id=template_version_id,
                lock=True,
            )
            if (
                version.status == TodoTemplateVersionStatus.PUBLISHED.value
                and template.published_version_id == version.template_version_id
            ):
                return version.to_record()
            if template.status == TodoTemplateStatus.ARCHIVED.value:
                raise TodoTemplateStateConflictError(
                    "archived Todo template cannot publish a version"
                )
            if version.status != TodoTemplateVersionStatus.DRAFT.value:
                raise TodoTemplateStateConflictError(
                    "only a draft Todo template version can be published"
                )
            now = datetime.now(timezone.utc)
            version.status = TodoTemplateVersionStatus.PUBLISHED.value
            version.draft_template_id = None
            version.published_by = published_by
            version.published_at = now
            template.published_version_id = version.template_version_id
            template.updated_by = published_by
            db.flush()
            db.refresh(version)
            return version.to_record()

    def publish_product_version(
        self,
        *,
        template_id: str,
        template_version_id: str,
        product_profile_id: int,
        published_by: str,
    ) -> TodoTemplateVersionRecord:
        if type(product_profile_id) is not int or product_profile_id < 1:
            raise TodoTemplateStateConflictError(
                "product_profile_id must be a positive integer"
            )
        with self._transaction() as db:
            template = self._require_template(db, template_id=template_id, lock=True)
            version = self._require_version(
                db,
                template_id=template_id,
                template_version_id=template_version_id,
                lock=True,
            )
            if (
                version.status == TodoTemplateVersionStatus.PUBLISHED.value
                and template.published_version_id == version.template_version_id
                and version.product_profile_id == product_profile_id
            ):
                return version.to_record()
            if version.product_profile_id not in (None, product_profile_id):
                raise TodoTemplateStateConflictError(
                    "Todo template version is already bound to another ProductProfile"
                )
            if template.status == TodoTemplateStatus.ARCHIVED.value:
                raise TodoTemplateStateConflictError(
                    "archived Todo template cannot publish a version"
                )
            if version.status != TodoTemplateVersionStatus.DRAFT.value:
                raise TodoTemplateStateConflictError(
                    "only a draft Todo template version can be published"
                )
            now = datetime.now(timezone.utc)
            version.product_profile_id = product_profile_id
            version.status = TodoTemplateVersionStatus.PUBLISHED.value
            version.draft_template_id = None
            version.published_by = published_by
            version.published_at = now
            template.published_version_id = version.template_version_id
            template.updated_by = published_by
            db.flush()
            db.refresh(version)
            return version.to_record()

    def set_template_status(
        self,
        *,
        template_id: str,
        status: TodoTemplateStatus,
        updated_by: str,
    ) -> TodoTemplateRecord:
        with self._transaction() as db:
            template = self._require_template(db, template_id=template_id, lock=True)
            template.status = status.value
            template.updated_by = updated_by
            db.flush()
            db.refresh(template)
            return template.to_record()
