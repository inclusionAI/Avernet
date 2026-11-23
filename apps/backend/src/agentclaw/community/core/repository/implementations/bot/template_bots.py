import json
from typing import Any

from injector import inject
from sqlalchemy import (
    BigInteger,
    SQLColumnExpression,
    String,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, Subquery

from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.core.devices.repository.models import EntityDeviceBinding
from agentclaw.community.core.repository.implementations.bot.stage_catalog import (
    _binding_projection,
    _latest_id,
)
from agentclaw.community.core.repository.protocols.bot.template_bots import (
    TemplateBotRepositoryProtocol,
)
from agentclaw.community.core.service_bot.repository.models import BotPublishModel
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant
from agentclaw.community.utils.env_utils import get_current_env


def _json_text(session: Session, document: SQLColumnExpression[str], path: str) -> ColumnElement[str]:
    safe = case((func.json_valid(document) == 1, document), else_='{}')
    extracted = func.json_extract(safe, path)
    if session.get_bind().dialect.name == 'sqlite':
        return case((func.json_type(safe, path) == 'text', extracted), else_=None)
    return case((func.json_type(extracted) == 'STRING', func.json_unquote(extracted)), else_=None)


def _version_id(
    session: Session,
    document: SQLColumnExpression[str],
    path: str = '$.template_version_id',
) -> ColumnElement[int]:
    safe = case((func.json_valid(document) == 1, document), else_='{}')
    extracted = func.json_extract(safe, path)
    if session.get_bind().dialect.name != 'sqlite':
        extracted = func.json_unquote(extracted)
    decimal = cast(extracted, String)
    return case((decimal.regexp_match(r'^[0-9]{1,15}$'), cast(decimal, BigInteger)), else_=None)


class TemplateBotRepository(TemplateBotRepositoryProtocol):
    @inject
    def __init__(self, db: DatabasePlugin) -> None:
        self.db = db

    def _scope(self, session: Session, template_key: str) -> ColumnElement[bool]:
        return and_(
            BotModel.is_delete == 0,
            BotModel.env == get_current_env(),
            BotModel.avernet_tenant == get_current_avernet_tenant(),
            _json_text(session, TemplateModel.ext, '$.template_key') == template_key,
        )

    def _rows(self, session: Session, template_key: str) -> Subquery:
        base = [
            BotModel.bot_id.label('bot_id'),
            BotModel.owner_id.label('owner_id'),
            BotModel.owner_name.label('owner_name'),
            BotModel.bot_name.label('bot_name'),
            BotModel.active_engine.label('engine'),
            BotModel.bot_type.label('bot_type'),
            BotModel.env.label('env'),
            BotModel.status.label('status'),
        ]
        draft = (
            select(
                *base,
                literal('draft').label('stage'),
                literal(None).label('publication_id'),
                _version_id(session, TemplateModel.ext).label('template_version_id'),
                _json_text(session, TemplateModel.ext, '$.template_version').label('template_version'),
            )
            .join(TemplateModel, TemplateModel.bot_id == BotModel.bot_id)
            .where(self._scope(session, template_key))
        )
        statements = [draft]
        for stage, binding_stage, status in [('prestable', 'verify', 'validating'), ('online', 'online', 'success')]:
            publication = BotPublishModel
            binding, _ = _binding_projection(session, publication.ext, binding_stage)
            expected_status = [
                publication.status == status,
                publication.id == _latest_id(BotModel.id, status, get_current_env(), BotModel),
            ]
            if stage == 'prestable':
                validating = exists(
                    select(1).where(
                        BotPublishModel.source_bot_pk == BotModel.id,
                        BotPublishModel.env == get_current_env(),
                        BotPublishModel.status == 'validating',
                    ).correlate(BotModel)
                )
                retained = exists(
                    select(1).where(
                        EntityDeviceBinding.id == binding,
                        EntityDeviceBinding.env == get_current_env(),
                        EntityDeviceBinding.status == 'ACTIVE',
                    ).correlate(publication)
                )
                expected = or_(
                    and_(*expected_status),
                    and_(
                        ~validating,
                        retained,
                        publication.status == 'success',
                        publication.id == _latest_id(BotModel.id, 'success', get_current_env(), BotModel),
                    ),
                )
            else:
                expected = and_(*expected_status)
            statements.append(
                select(
                    *base,
                    literal(stage).label('stage'),
                    publication.id.label('publication_id'),
                    _version_id(session, publication.ext, '$.template_config.template_version_id').label(
                        'template_version_id'
                    ),
                    _json_text(session, publication.ext, '$.template_config.template_version').label(
                        'template_version'
                    ),
                )
                .join(TemplateModel, TemplateModel.bot_id == BotModel.bot_id)
                .join(publication, publication.source_bot_pk == BotModel.id)
                .where(
                    self._scope(session, template_key),
                    BotModel.bot_type == 'service',
                    publication.env == get_current_env(),
                    binding.is_not(None),
                    expected,
                )
            )
        return union_all(*statements).subquery()

    def query(
        self,
        *,
        template_key: str,
        offset: int,
        limit: int,
        search: str = '',
        stage: str = '',
        outdated_before: int | None = None,
    ) -> dict[str, Any]:
        with self.db.orm_session() as session:
            rows = self._rows(session, template_key)
            filters = []
            if search:
                filters.append(or_(
                    *(rows.c[key].contains(search, autoescape=True)
                      for key in ('bot_id', 'bot_name', 'owner_id', 'owner_name'))
                ))
            if stage:
                filters.append(rows.c.stage == stage)
            if outdated_before is not None:
                filters.append(rows.c.template_version_id < outdated_before)
            total = session.execute(select(func.count()).select_from(rows).where(*filters)).scalar_one()
            statement = (
                select(rows)
                .where(*filters)
                .order_by(rows.c.owner_id, rows.c.bot_id, rows.c.stage, rows.c.publication_id)
                .offset(offset)
                .limit(limit)
            )
            items = session.execute(statement).mappings().all()
            return {'total': total, 'items': [dict(item) for item in items]}

    def snapshot(self, *, bot_id: str, owner_id: str, template_key: str) -> dict[str, Any] | None:
        with self.db.orm_session() as session:
            document = (
                session.query(TemplateModel.ext)
                .join(BotModel, BotModel.bot_id == TemplateModel.bot_id)
                .filter(
                    self._scope(session, template_key),
                    BotModel.bot_id == bot_id,
                    BotModel.owner_id == owner_id,
                )
                .one_or_none()
            )
            return json.loads(document.ext) if document else None

    def statuses(self, *, template_key: str, targets: list[tuple[str, str]]) -> list[dict[str, Any]]:
        addresses = list(dict.fromkeys(targets))
        if not addresses:
            return []
        restart_fields = ('operation_id', 'phase', 'failure_phase', 'backup_warning')
        with self.db.orm_session() as session:
            statement = (
                select(
                    BotModel.bot_id,
                    BotModel.owner_id,
                    BotModel.status,
                    BotModel.active_engine,
                    BotModel.template_type,
                    BotModel.binding_id,
                    BotModel.device_id,
                    EntityDeviceBinding.status.label('binding_status'),
                    EntityDeviceBinding.device_provider.label('device_provider'),
                    _json_text(session, BotModel.ext, '$.start_status').label('start_status'),
                    _json_text(session, BotModel.ext, '$.start_message').label('start_message'),
                    *(
                        _json_text(session, BotModel.ext, '$.coding_restart.' + field).label('restart_' + field)
                        for field in restart_fields
                    ),
                )
                .join(TemplateModel, TemplateModel.bot_id == BotModel.bot_id)
                .outerjoin(EntityDeviceBinding, and_(
                    EntityDeviceBinding.id == BotModel.binding_id,
                    EntityDeviceBinding.env == get_current_env(),
                ))
                .where(
                    self._scope(session, template_key),
                    or_(*(
                        and_(BotModel.bot_id == bot_id, BotModel.owner_id == owner_id)
                        for bot_id, owner_id in addresses
                    )),
                )
                .order_by(BotModel.owner_id, BotModel.bot_id)
            )
            items = []
            for row in session.execute(statement).mappings():
                bot = {key: row[key] for key in (
                    'bot_id', 'owner_id', 'status', 'active_engine', 'template_type', 'binding_id', 'device_id',
                )}
                bot['ext'] = {
                    'start_status': row['start_status'],
                    'start_message': row['start_message'],
                    'coding_restart': {field: row['restart_' + field] for field in restart_fields},
                }
                if row['binding_status'] is not None:
                    bot['device_binding'] = {
                        'status': row['binding_status'],
                        'device_provider': row['device_provider'],
                    }
                items.append(bot)
            return items
