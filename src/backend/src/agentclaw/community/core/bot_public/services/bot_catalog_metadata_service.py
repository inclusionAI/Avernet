"""BCS adapter for the catalog metadata port."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataUnavailableError,
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
        caller: BotCatalogCaller,
        request_id: str,
    ) -> Sequence[BotCatalogMetadata]:
        """Read one BCS result page without exposing BCS response data."""
        del caller
        params: dict[str, str | int] = {
            "offset": (page - 1) * page_size,
            "limit": page_size,
            "tc_bot": True,
        }
        if search and search.strip():
            params["q"] = search
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

            seen: set[BotCatalogAddress] = set()
            metadata: list[BotCatalogMetadata] = []
            for item in payload["items"]:
                if not isinstance(item, Mapping) or item.get("actor_kind") != "bot":
                    raise BotCatalogMetadataUnavailableError()
                address = self._address_from_bot_uuid(item.get("bot_uuid"))
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
            "[BcsBotCatalogMetadataService.search] request_id=%s result_count=%s",
            request_id,
            len(metadata),
        )
        return metadata

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
