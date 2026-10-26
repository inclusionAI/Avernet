"""ManualConsentPassportPlugin — HTTP client for the simulator's AgentPass mock.

The corp ``PassportPlugin`` brokers agent-passport credentials through the
tcauthmng TR facade over Hessian/Layotto. This community plugin is the
simulator-stack counterpart: it issues plain HTTP/JSON calls against the
mock-services AgentPass mock, whose base URL arrives as ``consent_base_url``
from ``user_config.community_identity`` (deployment-shaped example:
``http://mock-services-simulator:31270/agentpass``). The
mock service owns everything the registry used to hold in-process — the
application registry, the consent page a human clicks, and the approve /
reject decisions — so this plugin owns no durable state itself: every
facade answer below is the mock's answer, mapped onto the
``PassportPlugin`` contract.

The prod passport policy is preserved end to end:
``apply_agent_passport`` returns a PENDING application plus the mock's
consent URLs (``token`` stays null, and no headless caller anywhere can
flip the verdict — only the human surface can, and that surface belongs
to the mock service, never to this process), while
``apply_first_agent_passport`` keeps the trusted-bootstrap grant and
issues the owner's first passport inline. A PENDING application that
nobody attends dies by the mock's authorization window, which this
plugin asks for explicitly per apply.

Every read leg keeps the "still propagating" reading: a key the mock
does not know — or one whose authorization has not completed — answers
``None``, never an invented verdict. A transport failure or an error
envelope is a different animal: both raise ``RuntimeError`` so a broken
or absent mock fails loudly instead of quietly answering "not yet".

Every remaining ``PassportPlugin`` leg (update / destroy / freeze /
reissue / online / offline / save_sub_resources …) is the stateless
community surface and delegates, unchanged, to a composed
:class:`SelfIssuedPassportPlugin`.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.passport import (
    CliItem,
    extract_cli_items,
    validate_passport_target_env,
)
from agentclaw.community.plugins.community.passport import SelfIssuedPassportPlugin
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


logger = get_logger()

APPLICATION_STATUS_PENDING = "PENDING"
APPLICATION_STATUS_ISSUED = "ISSUED"
APPLICATION_STATUS_REJECTED = "REJECTED"

# The mock's auth is lax and presence-only: every POST body carries
# ``appKey`` + ``source`` and every GET query carries ``source`` + ``code``.
# The values themselves are never verified.
_AUTH_APP_KEY = "agentpass-sim"
_AUTH_SOURCE = "sim"
_AUTH_CODE = "agentpass-sim"

_TIMEOUT_SECONDS = 10

#: The authorization window each apply asks the mock to hold a PENDING
#: application open for — the budget the durable creation job's expiry
#: backstops, never a local expiry this plugin would invent.
_APPLY_WINDOW_SECONDS = 180


class ManualConsentPassportPlugin:
    """Community passport that defers every apply to the mock's human gate.

    Only ``CommunityIdentityModule`` names this class, and only when
    ``user_config.community_identity.passport_mode`` is
    ``manual_consent``; every other composition holds
    :class:`SelfIssuedPassportPlugin`. The class conforms to the
    ``PassportPlugin`` Protocol structurally and owns exactly its
    interactive legs — the two applies and the three facade reads — over
    HTTP; everything else answers through the composed community
    self-issued instance rather than a second nominal base.
    """

    def __init__(self, consent_base_url: str):
        self._base_url = consent_base_url.rstrip("/")
        self._issued_base = SelfIssuedPassportPlugin()

    def __getattr__(self, name: str):
        # PassportPlugin legs this class does not own (update / freeze /
        # destroy / reissue / online / ... — the stateless community
        # surface) answer through the composed self-issued base. Only
        # missing attributes reach here, so the owned apply/query legs
        # above keep their own dispatch; private lookups stay errors.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._issued_base, name)

    # ── PassportPlugin: the apply side ───────────────────────────────────────

    def apply_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
    ) -> dict[str, Any] | None:
        tenant = get_current_avernet_tenant()
        status, body = self._request(
            "POST",
            "/facade/applyAgentPassport",
            body={
                "bot_id": bot_id,
                "owner_workno": owner_workno,
                "tenant": tenant,
                "mcp_codes": mcp_codes,
                "window_seconds": _APPLY_WINDOW_SECONDS,
            },
        )
        data = self._require_success(status, body, "applyAgentPassport")
        logger.info(
            "[ManualConsent] applyAgentPassport bot_id=%s owner=%s tenant=%s "
            "application=%s status=%s",
            bot_id,
            owner_workno,
            tenant,
            data.get("application_id"),
            data.get("status"),
        )
        return self._apply_result(data)

    def apply_first_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        mcp_codes: list[str],
        bot_name: str | None = None,
        bot_desc: str | None = None,
        bot_llm: str | None = None,
        engine_type: str | None = None,
        access_mode: str | None = None,
        device_token: str | None = None,
        workspace_path: str | None = None,
        admins: list[str] | None = None,
        cli_items: list[CliItem] | None = None,
        *,
        target_env: str | None = None,
    ) -> dict[str, Any] | None:
        validate_passport_target_env(target_env)
        tenant = get_current_avernet_tenant()
        status, body = self._request(
            "POST",
            "/facade/applyFirstAgentPassport",
            body={
                "bot_id": bot_id,
                "owner_workno": owner_workno,
                "tenant": tenant,
            },
        )
        data = self._require_success(
            status, body, "applyFirstAgentPassport"
        )
        logger.info(
            "[ManualConsent] applyFirstAgentPassport bot_id=%s owner=%s "
            "tenant=%s → ISSUED",
            bot_id,
            owner_workno,
            tenant,
        )
        return self._apply_result(data)

    # ── PassportPlugin: the read side ────────────────────────────────────────

    def query_auth_status(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        validate_passport_target_env(target_env)
        status, body = self._request(
            "GET",
            "/facade/queryAuthStatus",
            params=self._query_params(bot_id, owner_workno),
        )
        data = self._optional_success(status, body)
        if data is None:
            return None
        # ``application_id`` lets a run-surface consumer address the
        # decision surface from this one answer; the empty string is
        # the still-propagating spelling for an answer naming none.
        return {
            "status": data["status"],
            "application_id": str(data.get("application_id") or ""),
        }

    def query_token(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> str | None:
        validate_passport_target_env(target_env)
        status, body = self._request(
            "GET",
            "/facade/queryToken",
            params=self._query_params(bot_id, owner_workno),
        )
        data = self._optional_success(status, body)
        if data is None:
            return None
        return data["token"]

    def query_agent_passport(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any] | None:
        validate_passport_target_env(target_env)
        status, body = self._request(
            "GET",
            "/facade/queryAgentPassport",
            params=self._query_params(bot_id, owner_workno),
        )
        data = self._optional_success(status, body)
        if data is None:
            return None
        return data

    def query_passport_clis(
        self,
        bot_id: str,
        owner_workno: str,
        *,
        target_env: str | None = None,
        entity_id: str | None = None,
    ) -> list[CliItem]:
        # Compose CLI grants off THIS class's passport facts, not the
        # base's unconditional ISSUED shape, so post-approval answers
        # mirror SelfIssued exactly while pending/rejected answer empty.
        passport_facts = self.query_agent_passport(
            bot_id, owner_workno, target_env=target_env, entity_id=entity_id,
        )
        if not passport_facts:
            return []
        return extract_cli_items(passport_facts)

    # ── transport ─────────────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """Send one facade call; return ``(HTTP status, decoded body)``.

        Transport-level failures (refused connections, DNS, timeouts)
        raise ``RuntimeError`` — callers must be able to tell "the mock
        answered 404" apart from "the mock was never reached". An
        HTTP-level error status is a real answer from the mock and is
        returned for the caller to interpret.
        """
        url = f"{self._base_url}{path}"
        if params is not None:
            query = dict(params)
            query["source"] = _AUTH_SOURCE
            query["code"] = _AUTH_CODE
            url = f"{url}?{urlencode(query)}"
        if body is not None:
            payload = dict(body)
            payload["appKey"] = _AUTH_APP_KEY
            payload["source"] = _AUTH_SOURCE
            data = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            data = None
            headers = {}
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                status = response.status
                raw = response.read()
        except HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read()
            except OSError:  # pragma: no cover — error body already drained
                raw = b""
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"mock-services AgentPass facade at {self._base_url} is "
                f"unreachable ({method} {path}): {exc}"
            ) from exc
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            decoded = None
        return status, decoded

    def _query_params(self, bot_id: str, owner_workno: str) -> dict[str, str]:
        """The business params every facade GET carries; ``_request`` adds auth."""
        return {
            "bot_id": bot_id,
            "owner_workno": owner_workno,
            "tenant": get_current_avernet_tenant(),
        }

    def _require_success(
        self,
        status: int,
        body: Any,
        route: str,
    ) -> dict[str, Any]:
        """Unwrap the success envelope for an apply, or fail loud.

        An apply is a write the caller cannot retry around semantically:
        refused, malformed, or failed answers raise ``RuntimeError``
        rather than degrading into a silent "no passport".
        """
        data = self._optional_success(status, body)
        if data is None:
            raise RuntimeError(
                f"mock-services AgentPass refused {route} "
                f"(status={status}, body={body!r})"
            )
        return data

    @staticmethod
    def _optional_success(status: int, body: Any) -> dict[str, Any] | None:
        """The envelope's ``data`` on success; ``None`` on any miss/failure.

        ``None`` is the pre-existing "still propagating" answer for reads:
        a 404 (the mock's unknown-key verdict) and a failure envelope
        both read as "no answer yet" rather than as a fault of this
        process.
        """
        if status == 200 and isinstance(body, dict) and body.get("success") is True:
            data = body.get("data")
            if isinstance(data, dict):
                return data
        return None

    @staticmethod
    def _apply_result(data: dict[str, Any]) -> dict[str, Any]:
        # The apply contract of the PassportPlugin Protocol: a token plus
        # agent code once issued, else the consent/redirect URLs of a
        # pending application. Anything the mock said beyond these four
        # fields (application_id, deadline, ...) belongs to it alone.
        return {
            "token": data["token"],
            "agent_code": data["agent_code"],
            "iframe_url": data["iframe_url"],
            "redirect_url": data["redirect_url"],
        }


__all__ = [
    "APPLICATION_STATUS_ISSUED",
    "APPLICATION_STATUS_PENDING",
    "APPLICATION_STATUS_REJECTED",
    "ManualConsentPassportPlugin",
]