"""Local user machine repository ORM implementation."""

from datetime import datetime

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._orm_model import LocalUserMachineModel
from ._protocol import (
    LocalUserMachineRepository,
)
from ._record import LocalUserMachineRecord

log = get_logger("orm-repository")


class OrmLocalUserMachineRepository(OrmConnectionMixin, LocalUserMachineRepository):
    def __init__(self, database) -> None:
        self._database = database

    @with_orm_session
    def insert_machine(
        self,
        *,
        template_id: int,
        user_id: str,
        machine_id: str,
        machine_info: dict | None = None,
        last_heartbeat: datetime,
        connected_server_instance: str,
        status: str,
        env: str,
    ) -> int:
        log.info(
            "insert_machine: machine_id=%s, user_id=%s, env=%s, status=%s",
            machine_id,
            user_id,
            env,
            status,
        )
        import json

        row = LocalUserMachineModel(
            template_id=template_id,
            user_id=user_id,
            machine_id=machine_id,
            machine_info=json.dumps(machine_info, ensure_ascii=False)
            if machine_info
            else None,
            last_heartbeat=last_heartbeat,
            connected_server_instance=connected_server_instance,
            status=status,
            env=env,
        )
        self._session.add(row)
        self._session.flush()
        result = int(row.id)
        log.info("[local-user-machine:insert_machine] result: id=%s", result)
        return result

    @with_orm_session
    def get_by_machine_id(
        self, machine_id: str, env: str
    ) -> LocalUserMachineRecord | None:
        log.info("get_by_machine_id: machine_id=%s, env=%s", machine_id, env)
        row = (
            self._session.query(LocalUserMachineModel)
            .filter(
                LocalUserMachineModel.machine_id == machine_id,
                LocalUserMachineModel.env == env,
            )
            .first()
        )
        record = row.to_record() if row else None
        log.info(
            "[local-user-machine:get_by_machine_id] result: %s",
            record.id if record else "None",
        )
        return record

    @with_orm_session
    def list_by_user_id(self, user_id: str, env: str) -> list[LocalUserMachineRecord]:
        log.info("list_by_user_id: user_id=%s, env=%s", user_id, env)
        rows = (
            self._session.query(LocalUserMachineModel)
            .filter(
                LocalUserMachineModel.user_id == user_id,
                LocalUserMachineModel.env == env,
            )
            .order_by(LocalUserMachineModel.last_heartbeat.desc())
            .all()
        )
        items = [r.to_record() for r in rows]
        log.info("[local-user-machine:list_by_user_id] result: %s rows", len(items))
        return items

    @with_orm_session
    def update_heartbeat(self, machine_id: str, env: str, timestamp: datetime) -> None:
        log.info("update_heartbeat: machine_id=%s, env=%s", machine_id, env)
        from sqlalchemy import func

        self._session.query(LocalUserMachineModel).filter(
            LocalUserMachineModel.machine_id == machine_id,
            LocalUserMachineModel.env == env,
        ).update(
            {"last_heartbeat": timestamp, "gmt_modified": func.now()},
            synchronize_session=False,
        )
        log.info("[local-user-machine:update_heartbeat] result: done")

    @with_orm_session
    def update_status(self, machine_id: str, env: str, status: str) -> None:
        log.info(
            "update_status: machine_id=%s, env=%s, status=%s", machine_id, env, status
        )
        from sqlalchemy import func

        self._session.query(LocalUserMachineModel).filter(
            LocalUserMachineModel.machine_id == machine_id,
            LocalUserMachineModel.env == env,
        ).update(
            {"status": status, "gmt_modified": func.now()},
            synchronize_session=False,
        )
        log.info("[local-user-machine:update_status] result: done")

    @with_orm_session
    def update_instance(self, machine_id: str, env: str, instance_id: str) -> None:
        log.info(
            "update_instance: machine_id=%s, env=%s, instance_id=%s",
            machine_id,
            env,
            instance_id,
        )
        from sqlalchemy import func

        self._session.query(LocalUserMachineModel).filter(
            LocalUserMachineModel.machine_id == machine_id,
            LocalUserMachineModel.env == env,
        ).update(
            {"connected_server_instance": instance_id, "gmt_modified": func.now()},
            synchronize_session=False,
        )
        log.info("[local-user-machine:update_instance] result: done")

    @with_orm_session
    def update_machine_info(self, machine_id: str, env: str, info: dict | None) -> None:
        log.info("update_machine_info: machine_id=%s, env=%s", machine_id, env)
        import json

        from sqlalchemy import func

        self._session.query(LocalUserMachineModel).filter(
            LocalUserMachineModel.machine_id == machine_id,
            LocalUserMachineModel.env == env,
        ).update(
            {
                "machine_info": json.dumps(info, ensure_ascii=False) if info else None,
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[local-user-machine:update_machine_info] result: done")

    @with_orm_session
    def update_route_info(self, machine_id: str, env: str, route_info: dict) -> None:
        log.info("update_route_info: machine_id=%s, env=%s", machine_id, env)
        import json

        from sqlalchemy import func

        self._session.query(LocalUserMachineModel).filter(
            LocalUserMachineModel.machine_id == machine_id,
            LocalUserMachineModel.env == env,
        ).update(
            {
                "connected_route_info": json.dumps(route_info, ensure_ascii=False),
                "gmt_modified": func.now(),
            },
            synchronize_session=False,
        )
        log.info("[local-user-machine:update_route_info] result: done")

    @with_orm_session
    def clear_route_info(self, machine_id: str, env: str) -> None:
        log.info("clear_route_info: machine_id=%s, env=%s", machine_id, env)
        from sqlalchemy import func

        self._session.query(LocalUserMachineModel).filter(
            LocalUserMachineModel.machine_id == machine_id,
            LocalUserMachineModel.env == env,
        ).update(
            {"connected_route_info": None, "gmt_modified": func.now()},
            synchronize_session=False,
        )
        log.info("[local-user-machine:clear_route_info] result: done")

    @with_orm_session
    def get_route_info(self, machine_id: str, env: str) -> dict | None:
        log.info("get_route_info: machine_id=%s, env=%s", machine_id, env)
        import json

        row = (
            self._session.query(LocalUserMachineModel.connected_route_info)
            .filter(
                LocalUserMachineModel.machine_id == machine_id,
                LocalUserMachineModel.env == env,
            )
            .first()
        )
        if not row or row[0] is None:
            log.info("[local-user-machine:get_route_info] result: None")
            return None
        try:
            result = json.loads(row[0]) if isinstance(row[0], str) else row[0] or None
            log.info("[local-user-machine:get_route_info] result: %s", result)
            return result
        except (json.JSONDecodeError, TypeError):
            log.info("[local-user-machine:get_route_info] result: None (parse error)")
            return None
