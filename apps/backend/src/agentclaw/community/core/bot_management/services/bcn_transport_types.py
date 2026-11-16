"""Typed transport definitions shared by the BCN transport surface.

Mechanically split from ``bcn_service.py`` (which stays the single public
facade and import path): the typed error hierarchy and the shared
provider-config seam live here so both the facade and
``bcn_roster.BcnRosterMixin`` can use them without the mixin importing the
facade. ``bcn_service.py`` re-exports every public name below so historical
``from ...bcn_service import BcnServiceError`` imports keep working.

Typed request failures (all subclass :class:`BcnServiceError`, so existing
``except BcnServiceError`` clauses keep catching everything):

- :class:`BcnBotMissingError` — HTTP 404, the addressed bot does not exist;
- :class:`BcnUnavailableError` — HTTP 503, BCN cannot serve the request;
- :class:`BcnRequestError` — any other failure with a stable machine
  classification (``kind`` + ``code``), e.g. ``http_403`` / ``timeout``.

:func:`bcn_http_error` converts a captured HTTP status to the typed error at
each transport capture point. Errors built by :func:`bcn_http_error` — i.e. the
typed hierarchy — carry the fixed code / status in their message only, never
a raw response body. Application-level (2xx-body) errors raised by the facade
outside this hierarchy intentionally keep the BCN server message, as pinned by
``test_envelope_failure_raises_with_server_message``.
"""

from typing import TYPE_CHECKING, Optional

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.di.config import BcnConfig

logger = get_logger()


class BcnServiceError(Exception):
    """BCN service error."""

    pass


class BcnBotMissingError(BcnServiceError):
    """The addressed BCN bot does not exist (HTTP 404 at the transport)."""

    pass


class BcnUnavailableError(BcnServiceError):
    """BCN is currently unavailable (HTTP 503 at the transport)."""

    pass


class BcnRequestError(BcnServiceError):
    """A BCN request failed with a stable, machine-readable classification.

    ``kind`` reuses the existing ``DeliveryFailure`` enum so downstream
    dispatchers keep one failure taxonomy. ``code`` is a fixed token derived
    from the transport capture point (``http_403``, ``timeout``, ``unknown``);
    callers distinguish missing / unavailable / other outcomes via the type
    and these two attributes instead of parsing message strings.
    """

    def __init__(self, kind: DeliveryFailure, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self._kind = kind
        self._code = code

    @property
    def kind(self) -> DeliveryFailure:
        """Stable classification of the failed send (never ``None``)."""
        return self._kind

    @property
    def code(self) -> str:
        """Fixed machine code, e.g. ``http_403`` / ``timeout`` / ``unknown``."""
        return self._code


def bcn_http_error(message_prefix: str, status: Optional[int]) -> BcnServiceError:
    """Build the typed transport error for a captured HTTP status.

    Fixed mapping (no response bodies are ever embedded):

    - 404 → :class:`BcnBotMissingError` (the endpoint semantics say missing);
    - 503 → :class:`BcnUnavailableError`;
    - other 4xx → :class:`BcnRequestError` (_kind_ REJECTED,
      ``code=http_<status>``);
    - other 5xx or no response (``None``) → :class:`BcnRequestError`
      (_kind_ UNKNOWN, ``code=http_<status>``).

    Args:
        message_prefix: Caller-scoped message prefix, e.g.
            ``"BCN caller config sync HTTP error"``.
        status: Captured HTTP status code, or ``None`` when httpx raised
            without a usable response.

    Returns:
        The typed error instance; the caller must ``raise`` it.
    """
    rendered = status if status is not None else "N/A"
    if status == 404:
        return BcnBotMissingError(f"{message_prefix}: {rendered}")
    if status == 503:
        return BcnUnavailableError(f"{message_prefix}: {rendered}")
    kind = (
        DeliveryFailure.REJECTED
        if status is not None and 400 <= status < 500
        else DeliveryFailure.UNKNOWN
    )
    code = f"http_{status}" if status is not None else "http_unknown"
    return BcnRequestError(kind, code, f"{message_prefix}: {rendered}")


def _get_provider_config(
    env: str,
    config: "BcnConfig",
) -> Optional[dict[str, str]]:
    """Return the BCN provider config for ``env``, or None when there is none.

    claude_code 下行链路默认仅 prod / pre 注册到真实 BCN。local-k8s/simulator
    可用 ``user_config.bcn.provider_registration_enabled`` 显式打开 dev 注册，
    但仍复用同一标准 ``POST /providers/{provider_id}/bots`` 链路和同一
    ``provider_id`` / ``provider_admin_token`` 凭据，不引入备用接口。任一凭据
    为空视作未配置（返回 None），``register_provider_bot`` 据此跳过。
    """
    registration_allowed = env in ("prod", "pre") or (
        env == "dev" and config.provider_registration_enabled
    )
    if not registration_allowed:
        logger.info(
            "[BcnService._get_provider_config] env=%s provider registration "
            "disabled",
            env,
        )
        return None
    provider_id = config.provider_id
    provider_admin_token = (config.provider_admin_token or "").strip()
    if not provider_id or not provider_admin_token:
        logger.info(
            "[BcnService._get_provider_config] env=%s missing provider config "
            "provider_id_present=%s provider_admin_token_present=%s",
            env,
            bool(provider_id),
            bool(provider_admin_token),
        )
        return None

    logger.info(
        "[BcnService._get_provider_config] env=%s provider config resolved "
        "provider_id=%s provider_admin_token_present=True",
        env,
        provider_id,
    )
    return {
        "provider_id": provider_id,
        "provider_admin_token": provider_admin_token,
    }
