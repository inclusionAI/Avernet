"""BCS adapter for the catalog metadata port."""

from __future__ import annotations

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
            "tc_bot": True,
        }
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
        try:
            # COSEC: The injected BCS client supplies the configured upstream host and
            # this constant relative path prevents request data from selecting a target.
            response = self._http.get(
                "/bots/search", params=params, timeout=self._timeout
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
                address = self._address_from_bot_uuid(bot_uuid)
                if address is None or address in seen:
                    raise BotCatalogMetadataUnavailableError()
                is_friend = item.get("is_friend")
                # COSEC: Do not coerce an invalid upstream relationship state into
                # a caller-visible boolean value.
                if "is_friend" in item and not isinstance(is_friend, bool):
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
                        friend_ext=item.get("friend_ext"),
                        friend_check_in_strategy=item.get(
                            "friend_check_in_strategy"
                        ),
                        user_visibility=item.get("user_visibility"),
                    )
                )
        except BotCatalogMetadataUnavailableError:
            logger.warning(
                "[BcsBotCatalogMetadataService.search] request_id=%s "
                "failure=invalid_response",
                request_id,
            )
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            logger.warning(
                "[BcsBotCatalogMetadataService.search] request_id=%s "
                "failure=upstream_unavailable",
                request_id,
            )
            raise BotCatalogMetadataUnavailableError() from None
        except Exception:  # noqa: BLE001 - BCS failures must fail closed
            logger.warning(
                "[BcsBotCatalogMetadataService.search] request_id=%s "
                "failure=upstream_unavailable",
                request_id,
            )
            raise BotCatalogMetadataUnavailableError() from None
        logger.info(
            "[BcsBotCatalogMetadataService.search] request_id=%s result_count=%s "
            "filter_count=%s has_viewer=%s",
            request_id,
            len(metadata),
            self._filter_count(filters),
            filters is not None and filters.viewer_actor_id is not None,
        )
        return BotCatalogMetadataPage(total=total, items=metadata)

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
    def _address_from_bot_uuid(value: object) -> BotCatalogAddress | None:
        if not isinstance(value, str):
            return None
        parts = value.rsplit(":", 1)
        if len(parts) != 2:
            return None
        bot_id, entity_id = (part.strip() for part in parts)
        if not bot_id or not entity_id:
            return None
        return BotCatalogAddress(bot_id=bot_id, entity_id=entity_id)
