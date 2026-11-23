from dataclasses import dataclass


@dataclass(frozen=True)
class OpenApiBotOperationsConfig:
    allowed_apps: frozenset[tuple[str, str, str]] = frozenset()

    def allows(self, *, env: str, tenant: str, app_name: str | None) -> bool:
        return app_name is not None and (env, tenant, app_name) in self.allowed_apps
