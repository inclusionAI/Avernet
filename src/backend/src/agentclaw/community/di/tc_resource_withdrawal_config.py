"""Validated deployment settings. No credential values belong in YAML."""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ResourceWithdrawalConfig:
    enabled: bool = False
    base_url: str = ""
    secret_name: str = ""
    tenant: str = ""
    timeout_seconds: int = 10
    lease_seconds: int = 60
    poll_seconds: int = 5
    retry_base_seconds: int = 10
    retry_max_seconds: int = 3600
    max_attempts: int = 20

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("withdrawal_enabled_must_be_boolean")
        for name in ("base_url", "secret_name", "tenant"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or value != value.strip()
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError(f"withdrawal_invalid_{name}")
        for name in (
            "timeout_seconds",
            "lease_seconds",
            "poll_seconds",
            "retry_base_seconds",
            "retry_max_seconds",
            "max_attempts",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 86400:
                raise ValueError(f"withdrawal_invalid_{name}")
        if self.lease_seconds <= self.timeout_seconds * 2:
            raise ValueError("withdrawal_lease_must_exceed_twice_timeout")
        if self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("withdrawal_invalid_retry_range")
        if len(self.tenant) > 128:
            raise ValueError("withdrawal_tenant_too_long")
        if self.base_url:
            url = urlsplit(self.base_url)
            local_http = url.scheme == "http" and url.hostname in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
            if (
                not url.hostname
                or (url.scheme != "https" and not local_http)
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
            ):
                raise ValueError("withdrawal_base_url_must_be_https_origin_or_loopback")
            try:
                url.port
            except ValueError:
                raise ValueError("withdrawal_invalid_port") from None
        if self.enabled and not all((self.base_url, self.secret_name, self.tenant)):
            raise ValueError(
                "withdrawal_enabled_requires_origin_secret_name_and_tenant"
            )
