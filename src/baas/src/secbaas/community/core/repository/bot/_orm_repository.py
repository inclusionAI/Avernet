"""ORM-based bot repository for baas_bot table."""

import json
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRepository
from secbaas.community.logger import get_logger

from ._orm_model import BotModel
from ._protocol import BotRepository
from ._record import BotRecord

log = get_logger("orm-repository")


class OrmBotRepository(OrmConnectionMixin, BotRepository):
    def __init__(
        self,
        database,
        rel_repo: BotDeviceRelRepository,
    ) -> None:
        self._database = database
        self._rel_repo = rel_repo

    @with_orm_session
    def insert_bot(
        self,
        *,
        bot_uuid: str,
        tenant: str,
        env: str,
        domain: str,
        creator: str,
        modifier: str,
        status: str = "PENDING",
        name: str,
        description: str | None = None,
        template_uuid: str | None = None,
        replica_desired: int = 1,
        replica_minimum: int = 1,
        replica_maximum: int = 10,
        auto_scaling_enabled: int = 0,
        sla_grade: str = "standard",
        extra_config: dict[str, Any] | None = None,
    ) -> int:
        log.info(
            "insert_bot: bot_uuid=%s, tenant=%s, env=%s, name=%s",
            bot_uuid,
            tenant,
            env,
            name,
        )
        row = BotModel(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=env,
            domain=domain,
            creator=creator,
            modifier=modifier,
            status=status,
            name=name,
            description=description,
            template_uuid=template_uuid,
            replica_desired=replica_desired,
            replica_minimum=replica_minimum,
            replica_maximum=replica_maximum,
            auto_scaling_enabled=bool(auto_scaling_enabled),
            sla_grade=sla_grade,
            extra_config=json.dumps(extra_config, ensure_ascii=False)
            if extra_config
            else None,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[bot:insert_bot] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_id(self, bot_id: int, tenant: str, env: str) -> BotRecord | None:
        log.info("get_by_id: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        row = (
            self._session.query(BotModel)
            .filter(
                BotModel.id == bot_id,
                BotModel.tenant == tenant,
                BotModel.env == env,
                BotModel.is_deleted == 0,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info("[bot:get_by_id] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def get_by_id_including_deleted(
        self, bot_id: int, tenant: str, env: str
    ) -> BotRecord | None:
        log.info(
            "get_by_id_including_deleted: bot_id=%s, tenant=%s, env=%s",
            bot_id,
            tenant,
            env,
        )
        row = (
            self._session.query(BotModel)
            .filter(
                BotModel.id == bot_id,
                BotModel.tenant == tenant,
                BotModel.env == env,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[bot:get_by_id_including_deleted] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def get_by_bot_uuid(
        self, bot_uuid: str, tenant: str, env: str, status: str
    ) -> BotRecord | None:
        log.info(
            "get_by_bot_uuid: bot_uuid=%s, tenant=%s, env=%s, status=%s",
            bot_uuid,
            tenant,
            env,
            status,
        )
        row = (
            self._session.query(BotModel)
            .filter(
                BotModel.bot_uuid == bot_uuid,
                BotModel.tenant == tenant,
                BotModel.env == env,
                BotModel.status == status,
                BotModel.is_deleted == 0,
            )
            .order_by(BotModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info("[bot:get_by_bot_uuid] result: %s", record.id if record else "None")
        return record

    @with_orm_session
    def list_by_bot_uuid(self, bot_uuid: str, tenant: str, env: str) -> list[BotRecord]:
        log.info(
            "list_by_bot_uuid: bot_uuid=%s, tenant=%s, env=%s", bot_uuid, tenant, env
        )
        rows = (
            self._session.query(BotModel)
            .filter(
                BotModel.bot_uuid == bot_uuid,
                BotModel.tenant == tenant,
                BotModel.env == env,
                BotModel.is_deleted == 0,
            )
            .order_by(BotModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[bot:list_by_bot_uuid] result: %s rows", len(items))
        return items

    @with_orm_session
    def list_by_bot_uuid_including_deleted(
        self, bot_uuid: str, tenant: str, env: str
    ) -> list[BotRecord]:
        log.info(
            "list_by_bot_uuid_including_deleted: bot_uuid=%s, tenant=%s, env=%s",
            bot_uuid,
            tenant,
            env,
        )
        rows = (
            self._session.query(BotModel)
            .filter(
                BotModel.bot_uuid == bot_uuid,
                BotModel.tenant == tenant,
                BotModel.env == env,
            )
            .order_by(BotModel.id.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[bot:list_by_bot_uuid_including_deleted] result: %s rows", len(items))
        return items

    @with_orm_session
    def get_active_by_bot_uuid(
        self, bot_uuid: str, tenant: str, env: str
    ) -> BotRecord | None:
        log.info(
            "get_active_by_bot_uuid: bot_uuid=%s, tenant=%s, env=%s",
            bot_uuid,
            tenant,
            env,
        )
        rows = (
            self._session.query(BotModel)
            .filter(
                BotModel.bot_uuid == bot_uuid,
                BotModel.tenant == tenant,
                BotModel.env == env,
                BotModel.status == "ACTIVE",
                BotModel.is_deleted == 0,
            )
            .order_by(BotModel.id.desc())
            .limit(1)
            .all()
        )
        if len(rows) > 1:
            raise RuntimeError(
                f"Data integrity violation: multiple ACTIVE bots found for bot_uuid={bot_uuid}"
            )
        record = rows[0].to_record() if rows else None
        log.info(
            "[bot:get_active_by_bot_uuid] result: %s", record.id if record else "None"
        )
        return record

    @with_orm_session
    def get_active_by_bot_uuid_only(self, bot_uuid: str) -> BotRecord | None:
        """Get ACTIVE bot by bot_uuid only (no tenant/env filter).

        Filters: bot_uuid + status='ACTIVE' + is_deleted=0
        """
        log.info("get_active_by_bot_uuid_only: bot_uuid=%s", bot_uuid)
        row = (
            self._session.query(BotModel)
            .filter(
                BotModel.bot_uuid == bot_uuid,
                BotModel.status == "ACTIVE",
                BotModel.is_deleted == 0,
            )
            .order_by(BotModel.id.desc())
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[bot:get_active_by_bot_uuid_only] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def update_bot(
        self,
        *,
        bot_id: int,
        tenant: str,
        env: str,
        name: str | None = None,
        description: str | None = None,
        modifier: str | None = None,
        extra_config: dict[str, Any] | None = None,
        replica_desired: int | None = None,
    ) -> int:
        log.info("update_bot: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        from sqlalchemy import func as sa_func

        values: dict[str, Any] = {"gmt_modified": sa_func.now()}
        if name is not None:
            values["name"] = name
        if description is not None:
            values["description"] = description
        if modifier is not None:
            values["modifier"] = modifier
        if extra_config is not None:
            values["extra_config"] = json.dumps(extra_config, ensure_ascii=False)
        if replica_desired is not None:
            values["replica_desired"] = replica_desired
        result = (
            self._session.query(BotModel)
            .filter(
                BotModel.id == bot_id,
                BotModel.tenant == tenant,
                BotModel.env == env,
                BotModel.is_deleted == 0,
            )
            .update(values, synchronize_session=False)
        )
        result = int(result)
        log.info("[bot:update_bot] result: %s rows", result)
        return result

    @with_orm_session
    def update_status(
        self,
        *,
        bot_id: int,
        tenant: str,
        env: str,
        status: str,
        modifier: str,
    ) -> None:
        log.info(
            "update_status: bot_id=%s, tenant=%s, env=%s, status=%s",
            bot_id,
            tenant,
            env,
            status,
        )
        from sqlalchemy import func as sa_func

        self._session.query(BotModel).filter(
            BotModel.id == bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "status": status,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot:update_status] result: done")

    @with_orm_session
    def soft_delete(self, *, bot_id: int, tenant: str, env: str, modifier: str) -> None:
        log.info("soft_delete: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        from sqlalchemy import func as sa_func

        self._session.query(BotModel).filter(
            BotModel.id == bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": bot_id,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot:soft_delete] result: done")

    @with_orm_session
    def insert_bot_record(
        self,
        *,
        source_bot_id: int,
        tenant: str,
        env: str,
        status: str,
        extra_config: dict[str, Any] | None = None,
        name: str | None = None,
        template_uuid: str | None = None,
        modifier: str = "system",
    ) -> int:
        log.info(
            "insert_bot_record: source_bot_id=%s, tenant=%s, env=%s, status=%s",
            source_bot_id,
            tenant,
            env,
            status,
        )
        source = self.get_by_id(source_bot_id, tenant, env)
        if source is None:
            raise ValueError(f"Source bot not found: {source_bot_id}")

        if status == "PENDING":
            existing_pending = self.get_by_bot_uuid(
                source.bot_uuid, tenant, env, "PENDING"
            )
            if existing_pending is not None:
                try:
                    rel_repo = self._rel_repo
                    rel_repo.soft_delete_by_bot_id(
                        bot_id=existing_pending.id,
                        tenant=tenant,
                        env=env,
                        modifier=modifier,
                    )
                except Exception:
                    pass
                self.soft_delete(
                    bot_id=existing_pending.id,
                    tenant=tenant,
                    env=env,
                    modifier=modifier,
                )

        return self.insert_bot(
            bot_uuid=source.bot_uuid,
            tenant=source.tenant,
            env=source.env,
            domain=source.domain,
            creator=source.creator,
            modifier=modifier,
            status=status,
            name=name if name is not None else source.name,
            description=source.description,
            template_uuid=(
                template_uuid if template_uuid is not None else source.template_uuid
            ),
            replica_desired=source.replica_desired,
            replica_minimum=source.replica_minimum,
            replica_maximum=source.replica_maximum,
            auto_scaling_enabled=source.auto_scaling_enabled,
            sla_grade=source.sla_grade,
            extra_config=extra_config
            if extra_config is not None
            else source.extra_config,
        )

    @with_orm_session
    def list_bots(
        self,
        *,
        tenant: str,
        env: str,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[BotRecord]]:
        log.info(
            "list_bots: tenant=%s, env=%s, status=%s, page=%s",
            tenant,
            env,
            status,
            page,
        )
        from sqlalchemy import func as sa_func

        query = self._session.query(BotModel).filter(
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        )
        if status is not None:
            query = query.filter(BotModel.status == status)

        total = query.with_entities(sa_func.count(BotModel.id)).scalar()
        offset = (page - 1) * page_size
        rows = query.order_by(BotModel.id.desc()).offset(offset).limit(page_size).all()
        items = [r.to_record() for r in rows]
        log.info("[bot:list_bots] result: %s rows", len(items))
        return total, items

    @with_orm_session
    def complete_destroy(
        self, *, bot_id: int, tenant: str, env: str, modifier: str
    ) -> None:
        log.info("complete_destroy: bot_id=%s, tenant=%s, env=%s", bot_id, tenant, env)
        from sqlalchemy import func as sa_func

        self._session.query(BotModel).filter(
            BotModel.id == bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "status": "RELEASED",
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        self._session.query(BotModel).filter(
            BotModel.id == bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": bot_id,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot:complete_destroy] result: done")

    @with_orm_session
    def complete_stop(
        self,
        *,
        bot_id: int,
        tenant: str,
        env: str,
        modifier: str,
    ) -> None:
        log.info(
            "complete_stop: bot_id=%s, tenant=%s, env=%s",
            bot_id,
            tenant,
            env,
        )
        from sqlalchemy import func as sa_func

        self._session.query(BotModel).filter(
            BotModel.id == bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "status": "STOPPED",
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot:complete_stop] result: done")

    @with_orm_session
    def complete_update_transfer(
        self,
        *,
        old_bot_id: int,
        new_bot_id: int,
        device_uuids: list[str],
        domain: str,
        tenant: str,
        env: str,
        modifier: str,
    ) -> None:
        log.info(
            "complete_update_transfer: old_bot_id=%s, new_bot_id=%s, tenant=%s, env=%s",
            old_bot_id,
            new_bot_id,
            tenant,
            env,
        )
        from sqlalchemy import func as sa_func

        from secbaas.community.core.repository.bot_device_rel import BotDeviceRelModel

        self._session.query(BotDeviceRelModel).filter(
            BotDeviceRelModel.bot_id == old_bot_id,
            BotDeviceRelModel.tenant == tenant,
            BotDeviceRelModel.env == env,
            BotDeviceRelModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": BotDeviceRelModel.id,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )

        # 2. Create new relationships
        for device_uuid in device_uuids:
            rel = BotDeviceRelModel(
                bot_id=new_bot_id,
                device_uuid=device_uuid,
                tenant=tenant,
                env=env,
                domain=domain,
                creator=modifier,
                modifier=modifier,
            )
            self._session.add(rel)

        self._session.flush()

        # 3. Set old bot to RELEASED
        self._session.query(BotModel).filter(
            BotModel.id == old_bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "status": "RELEASED",
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )

        # 4. Soft-delete old bot
        self._session.query(BotModel).filter(
            BotModel.id == old_bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "is_deleted": old_bot_id,
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )

        # 5. Set new bot to ACTIVE
        self._session.query(BotModel).filter(
            BotModel.id == new_bot_id,
            BotModel.tenant == tenant,
            BotModel.env == env,
            BotModel.is_deleted == 0,
        ).update(
            {
                "status": "ACTIVE",
                "modifier": modifier,
                "gmt_modified": sa_func.now(),
            },
            synchronize_session=False,
        )
        log.info("[bot:complete_update_transfer] result: done")
