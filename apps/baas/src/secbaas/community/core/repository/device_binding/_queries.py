"""SQL query projections; policy and multi-repository branching live in services."""

import json
from typing import Any

from secbaas.community.core.repository import OrmConnectionMixin, with_orm_session
from secbaas.community.logger import get_logger

from ._record import DeviceBindingRecord

log = get_logger("orm")


class DeviceBindingQueriesMixin(OrmConnectionMixin):
    @with_orm_session
    def list_active_sandboxes_with_bot(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str | None = None,
        device_provider: str | None = None,
        sort_by: str = "id",
        sort_order: str = "desc",
    ) -> tuple[int, list[tuple[DeviceBindingRecord, dict[str, Any]]]]:
        valid_sort_fields = {"id", "gmt_create"}
        if sort_by not in valid_sort_fields:
            sort_by = "id"
        sort_order_sql = "DESC" if sort_order.lower() == "desc" else "ASC"

        where_clauses = [
            "d.status = 'ACTIVE'",
            "b.is_delete = 0",
            "d.device_props IS NOT NULL",
        ]
        params: list[Any] = []
        if env:
            where_clauses.append("d.env = :env")
            params.append({"name": "env", "value": env})
        if device_provider:
            where_clauses.append("d.device_provider = :device_provider")
            params.append({"name": "device_provider", "value": device_provider})

        where_sql = " AND ".join(where_clauses)

        count_sql = f"""
        SELECT COUNT(*)
        FROM ac_entity_device_binding d
        INNER JOIN ac_bots b ON d.device_id = b.device_id
        WHERE {where_sql}
        """
        param_dict = {p["name"]: p["value"] for p in params}

        from sqlalchemy import text

        total = self._session.execute(text(count_sql), param_dict).scalar() or 0

        offset = (page - 1) * page_size
        data_sql = f"""
        SELECT
            d.id, d.entity_id, d.entity_type, d.device_id, d.device_provider, d.env, d.device_props,
            d.status, d.apply_reason, d.applied_by,
            d.release_reason, d.released_by, d.released_at, d.last_alive_at,
            d.gmt_create, d.gmt_modified,
            b.bot_id, b.bot_name, b.status AS bot_status, b.active_engine,
            b.gmt_create AS bot_gmt_create, b.gmt_modified AS bot_gmt_modified
        FROM ac_entity_device_binding d
        INNER JOIN ac_bots b ON d.device_id = b.device_id
        WHERE {where_sql}
        ORDER BY d.{sort_by} {sort_order_sql}
        LIMIT :limit OFFSET :offset
        """
        param_dict["limit"] = page_size
        param_dict["offset"] = offset

        rows = self._session.execute(text(data_sql), param_dict).fetchall()

        items: list[tuple[DeviceBindingRecord, dict[str, Any]]] = []
        for row in rows:
            device_props_json = row[6]
            try:
                device_props = (
                    json.loads(device_props_json)
                    if isinstance(device_props_json, str)
                    else device_props_json
                )
            except (json.JSONDecodeError, TypeError):
                device_props = {}

            record = DeviceBindingRecord(
                id=row[0],
                entity_id=row[1],
                entity_type=row[2],
                device_id=row[3],
                device_provider=row[4],
                env=row[5],
                device_props=device_props,
                status=row[7],
                apply_reason=row[8],
                applied_by=row[9],
                release_reason=row[10],
                released_by=row[11],
                released_at=row[12],
                last_alive_at=row[13],
                gmt_create=row[14],
                gmt_modified=row[15],
            )
            bot_info = {
                "bot_id": row[16],
                "bot_name": row[17],
                "bot_status": row[18],
                "active_engine": row[19],
                "bot_created_at": row[20],
                "bot_modified_at": row[21],
            }
            items.append((record, bot_info))

        log.info(
            "[device-binding:list_active_sandboxes_with_bot] returned %s items, total=%s",
            len(items),
            total,
        )
        return total, items

    @with_orm_session
    def list_all_active_bot_device(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        env: str = "prod",
        bot_type: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        from sqlalchemy import text

        where_clauses = ["is_delete = 0", "env = :env", "status = 'ACTIVE'"]
        params: dict[str, Any] = {"env": env}
        if bot_type is not None:
            where_clauses.append("bot_type = :bot_type")
            params["bot_type"] = bot_type

        where_sql = " AND ".join(where_clauses)

        count_sql = f"SELECT COUNT(*) FROM ac_bots WHERE {where_sql}"
        total = self._session.execute(text(count_sql), params).scalar() or 0

        offset = (page - 1) * page_size
        data_sql = f"""
        SELECT bot_id, entity_id, binding_id, bot_type, active_engine, status
        FROM ac_bots
        WHERE {where_sql}
        ORDER BY id DESC
        LIMIT :limit OFFSET :offset
        """
        params["limit"] = page_size
        params["offset"] = offset

        rows = self._session.execute(text(data_sql), params).fetchall()
        items = [
            {
                "bot_id": row[0],
                "entity_id": row[1],
                "binding_id": row[2],
                "bot_type": row[3],
                "active_engine": row[4],
                "status": row[5],
            }
            for row in rows
        ]

        log.info(
            "[device-binding:list_all_active_bot_device] returned %s items, total=%s",
            len(items),
            total,
        )
        return total, items

    @with_orm_session
    def get_bot_binding(
        self,
        *,
        bot_id: str,
        entity_id: str,
        env: str = "prod",
    ) -> dict[str, Any] | None:
        from sqlalchemy import text

        sql = """
        SELECT b.bot_id, b.entity_id, b.binding_id, b.bot_type, b.active_engine, b.status,
               eb.device_provider
        FROM ac_bots b
        LEFT JOIN ac_entity_device_binding eb ON b.binding_id = eb.id
        WHERE b.bot_id = :bot_id AND b.entity_id = :entity_id
          AND b.is_delete = 0 AND b.status = 'ACTIVE' AND b.env = :env
        LIMIT 1
        """
        row = self._session.execute(
            text(sql), {"bot_id": bot_id, "entity_id": entity_id, "env": env}
        ).fetchone()

        if not row:
            log.info(
                "[device-binding:get_bot_binding] Bot not found: bot_id=%s, entity_id=%s",
                bot_id,
                entity_id,
            )
            return None

        result = {
            "bot_id": row[0],
            "entity_id": row[1],
            "binding_id": row[2],
            "bot_type": row[3],
            "active_engine": row[4],
            "status": row[5],
            "device_provider": row[6],
        }
        log.info(
            "[device-binding:get_bot_binding] result: bot_type=%s, binding_id=%s",
            result["bot_type"],
            result["binding_id"],
        )
        return result
