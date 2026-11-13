"""Bootstrap HTTP：onboard 与 visibility。

BCN 的 WebSocket 负责消息，Bot 名称/能力/可见性仍走公开 HTTP 契约。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import PlayerConfig


class OnboardingError(RuntimeError):
    def __init__(self, status: int | None, message: str, *, unauthorized: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.unauthorized = unauthorized


def _request_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
    timeout: float = 15.0,
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - endpoint comes from explicit config
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read(65536)
            if not raw:
                return {}
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail = error.read(512).decode("utf-8", "replace")
        except OSError:
            detail = ""
        raise OnboardingError(
            error.code,
            f"HTTP {error.code} from {method} {url}: {detail[:200]}",
            unauthorized=error.code == 401,
        ) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise OnboardingError(None, f"{method} {url} failed: {type(error).__name__}") from error


def _url(config: PlayerConfig, path: str) -> str:
    return config.bcs_url.rstrip("/") + path


def onboard_bot(config: PlayerConfig, *, bot_uuid: str, token: str) -> None:
    """注册名称/能力并设为 public，保证主持人和人类能选中该玩家。"""

    _request_json(
        "POST",
        _url(config, "/bots/onboard"),
        token=token,
        payload={
            "name": config.bot_name,
            "summary": config.summary,
            "skills": list(config.skills),
            "domains": list(config.domains),
            "scopes": list(config.scopes),
            "binding_channels": None,
        },
    )
    _request_json(
        "PUT",
        _url(config, f"/bots/{bot_uuid}/visibility"),
        token=token,
        payload={"visibility": "public"},
    )
