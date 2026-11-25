"""Validated deployment configuration; the only environment-reading boundary."""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = "local"
    database: Path
    signing_key_file: Path
    local_token_file: Path | None = None
    platform_factory: str = ""
    backend_principal_key_file: Path | None = None
    backend_tenant: str = ""
    backend_data_origin: str = ""  # optional direct Backend origin, not the browser Gateway
    host: str = "127.0.0.1"
    port: int = Field(default=8091, ge=1, le=65535)
    consumer: bool = True
    poll_interval_s: float = Field(default=1, gt=0)
    lease_seconds: int = Field(default=60, ge=3)
    heartbeat_s: float = Field(default=10, gt=0)
    job_endpoint: str = "http://127.0.0.1:8091/openapi/v1/evolution/jobs"

    @model_validator(mode="after")
    def validate_profile(self):
        if self.profile not in {"local", "production"}:
            raise ValueError("profile must be local or production")
        if str(self.database) == ":memory:" or not self.database.is_absolute():
            raise ValueError("database must be an absolute persistent path")
        if self.heartbeat_s >= self.lease_seconds / 2:
            raise ValueError("heartbeat must be less than half the lease duration")
        if self.profile == "local":
            if self.host not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError("local fixture profile binds loopback only")
            if self.local_token_file is None:
                raise ValueError("local profile requires a token file")
        elif not self.platform_factory or self.platform_factory.startswith("avernet_evolution.local"):
            raise ValueError("production requires an explicit reviewed platform factory")
        if self.profile == "production" and (
            self.backend_principal_key_file is None or not self.backend_tenant.strip()
        ):
            raise ValueError("production requires Backend principal key file and tenant")
        return self

    @classmethod
    def from_env(cls):
        # Reject misspelled Evolution settings instead of silently selecting a
        # default (especially a disabled consumer or the wrong job endpoint).
        names = {
            name.removeprefix("EVOLUTION_").lower(): value
            for name, value in os.environ.items()
            if name.startswith("EVOLUTION_")
        }
        return cls.model_validate(names)

    def signing_key(self) -> bytes:
        key = self.signing_key_file.read_bytes().strip()
        if len(key) < 32:
            raise ValueError("signing key must have at least 32 bytes")
        return key
