"""BCS adapter for the catalog metadata port."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

import httpx

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataUnavailableError,
    BotCatalogMetadataPage,
    BotCatalogSearchFilters,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.http_client import HttpClient

logger = get_logger()

_BCS_SEARCH_ROUTE = "/bots/search"
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "credential",
        "key",
        "password",
        "secret",
        "session",
        "session_id",
        "token",
    }
)


class BcsBotCatalogMetadataService:
    """Resolve the requested BCS catalog page into Backend address pairs."""

    def __init__(self, http_client: HttpClient, timeout: float = 30.0) -> None:
        self._http = http_client
        self._timeout = timeout

    def search_public_bot_metadata(
        self,
        *,
        search: str | None,
        page: int,
        page_size: int,
        bot_uuids: Sequence[str] = (),
        filters: BotCatalogSearchFilters | None = None,
        caller: BotCatalogCaller,
        request_id: str,
    ) -> BotCatalogMetadataPage:
        """Read one BCS result page without exposing BCS response data."""
        del caller
        params: dict[str, str | int] = {
            "offset": (page - 1) * page_size,
            "limit": page_size,
        }
        if filters is None or filters.viewer_actor_type != "bot":
            params["tc_bot"] = True
        if search and search.strip():
            params["q"] = search
        if bot_uuids:
            params["bot_uuids"] = ",".join(bot_uuids)
        if filters is not None:
            if filters.visibility:
                params["visibility"] = ",".join(filters.visibility)
            if filters.user_visibility:
                params["user_visibility"] = ",".join(filters.user_visibility)
            if filters.status is not None:
                params["status"] = filters.status
            if filters.viewer_actor_type is not None:
                params["viewer_actor_type"] = filters.viewer_actor_type
            if filters.viewer_actor_id is not None:
                params["viewer_actor_id"] = filters.viewer_actor_id
            if filters.friendship is not None:
                params["friendship"] = filters.friendship
        started_at = time.perf_counter()
        response: httpx.Response | None = None
        logger.info(
            "event=bcs_catalog_search.request request_id=%s route=%s offset=%s "
            "limit=%s search_present=%s filter_count=%s viewer_type=%s "
            "tc_bot_filter=%s",
            request_id,
            _BCS_SEARCH_ROUTE,
            params["offset"],
            params["limit"],
            bool(search and search.strip()),
            self._filter_count(filters),
            filters.viewer_actor_type if filters is not None else None,
            params.get("tc_bot"),
        )
        try:
            # COSEC: The injected BCS client supplies the configured upstream host and
            # this constant relative path prevents request data from selecting a target.
            response = self._http.get(
                _BCS_SEARCH_ROUTE, params=params, timeout=self._timeout
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("items"), list
            ):
                raise BotCatalogMetadataUnavailableError()
            total = payload.get("total")
            if isinstance(total, bool) or not isinstance(total, int) or total < 0:
                raise BotCatalogMetadataUnavailableError()

            seen: set[BotCatalogAddress] = set()
            metadata: list[BotCatalogMetadata] = []
            for item in payload["items"]:
                if not isinstance(item, Mapping) or item.get("actor_kind") != "bot":
                    raise BotCatalogMetadataUnavailableError()
                bot_uuid = item.get("bot_uuid")
                if not isinstance(bot_uuid, str):
                    raise BotCatalogMetadataUnavailableError()
                is_friend = item.get("is_friend")
                # COSEC: Do not coerce an invalid upstream relationship state into
                # a caller-visible boolean value.
                if "is_friend" in item and not isinstance(is_friend, bool):
                    raise BotCatalogMetadataUnavailableError()
                optional_strings = {
                    field_name: self._optional_string(item, field_name)
                    for field_name in ("name", "summary", "created_by", "status")
                }
                created_by = optional_strings["created_by"]
                if created_by is not None:
                    created_by = created_by.strip() or None
                    optional_strings["created_by"] = created_by
                address = self._address_from_bot_uuid(bot_uuid, created_by)
                if address is None or address in seen:
                    raise BotCatalogMetadataUnavailableError()
                seen.add(address)
                metadata.append(
                    BotCatalogMetadata(
                        address=address,
                        kind="bot",
                        bot_uuid=bot_uuid,
                        is_friend=is_friend,
                        visibility=item.get("visibility"),
                        is_online=item.get("is_online"),
                        actor_kind=item.get("actor_kind"),
                        friend_ext=self._redact_sensitive_fields(
                            item.get("friend_ext")
                        ),
                        friend_check_in_strategy=item.get(
                            "friend_check_in_strategy"
                        ),
                        user_visibility=item.get("user_visibility"),
                        **optional_strings,
                    )
                )
        except BotCatalogMetadataUnavailableError:
            logger.warning(
                "event=bcs_catalog_search.failed request_id=%s route=%s "
                "failure=invalid_response http_status=%s duration_ms=%.1f",
                request_id,
                _BCS_SEARCH_ROUTE,
                response.status_code if response is not None else None,
                (time.perf_counter() - started_at) * 1000,
            )
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            logger.warning(
                "event=bcs_catalog_search.failed request_id=%s route=%s "
                "failure=upstream_unavailable http_status=%s duration_ms=%.1f",
                request_id,
                _BCS_SEARCH_ROUTE,
                response.status_code if response is not None else None,
                (time.perf_counter() - started_at) * 1000,
            )
            raise BotCatalogMetadataUnavailableError() from None
        except Exception:  # noqa: BLE001 - BCS failures must fail closed
            logger.warning(
                "event=bcs_catalog_search.failed request_id=%s route=%s "
                "failure=upstream_unavailable http_status=%s duration_ms=%.1f",
                request_id,
                _BCS_SEARCH_ROUTE,
                response.status_code if response is not None else None,
                (time.perf_counter() - started_at) * 1000,
            )
            raise BotCatalogMetadataUnavailableError() from None
        logger.info(
            "event=bcs_catalog_search.succeeded request_id=%s route=%s "
            "http_status=%s duration_ms=%.1f result_count=%s total=%s",
            request_id,
            _BCS_SEARCH_ROUTE,
            response.status_code,
            (time.perf_counter() - started_at) * 1000,
            len(metadata),
            total,
        )
        return BotCatalogMetadataPage(total=total, items=metadata)

    @classmethod
    def _redact_sensitive_fields(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {
                key: None
                if isinstance(key, str) and cls._is_sensitive_field_name(key)
                else cls._redact_sensitive_fields(nested)
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [cls._redact_sensitive_fields(item) for item in value]
        return value

    @staticmethod
    def _is_sensitive_field_name(field_name: str) -> bool:
        normalized = field_name.strip().lower().replace("-", "_")
        return normalized in _SENSITIVE_FIELD_NAMES or normalized.endswith(
            ("_token", "_secret", "_password", "_credential", "_key", "_session")
        )

    @staticmethod
    def _optional_string(item: Mapping[object, object], field_name: str) -> str | None:
        value = item.get(field_name)
        if value is not None and not isinstance(value, str):
            raise BotCatalogMetadataUnavailableError()
        return value

    @staticmethod
    def _filter_count(filters: BotCatalogSearchFilters | None) -> int:
        if filters is None:
            return 0
        return sum(
            value is not None and value != ()
            for value in (
                filters.visibility,
                filters.user_visibility,
                filters.status,
                filters.viewer_actor_type,
                filters.viewer_actor_id,
                filters.friendship,
            )
        )

    @staticmethod
    def _address_from_bot_uuid(
        value: object, created_by: str | None = None
    ) -> BotCatalogAddress | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        bot_id, separator, suffix = normalized.rpartition(":")
        if not separator:
            bot_id = normalized
            suffix = ""
        else:
            bot_id = bot_id.strip()
        entity_id = (created_by or suffix).strip()
        if not bot_id or not entity_id:
            return None
        return BotCatalogAddress(bot_id=bot_id, entity_id=entity_id)
