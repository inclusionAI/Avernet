"""Generic HTTP write protection for domain-owned configuration keys."""
from fastapi import HTTPException

from agentclaw.community.api.system_config_service import ProtectedConfigKey, SystemConfigServiceProtocol
from agentclaw.community.di import Injected
from .schemas import DeleteCategoryRequest, DeleteConfigRequest, SetConfigRequest


def _check(category: str, config_key: str | None, protected: list[ProtectedConfigKey]) -> None:
    if any(key.matches(category, config_key) for key in protected):
        raise HTTPException(403, "Configuration is managed through its domain interface")


def protect_config_set(
    req: SetConfigRequest,
    protected: list[ProtectedConfigKey] = Injected(list[ProtectedConfigKey]),
) -> None:
    _check(req.category, req.config_key, protected)


def protect_config_delete(
    req: DeleteConfigRequest,
    protected: list[ProtectedConfigKey] = Injected(list[ProtectedConfigKey]),
) -> None:
    _check(req.category, req.config_key, protected)


def protect_category_delete(
    req: DeleteCategoryRequest,
    service: SystemConfigServiceProtocol = Injected(SystemConfigServiceProtocol),
    protected: list[ProtectedConfigKey] = Injected(list[ProtectedConfigKey]),
) -> None:
    if not protected:
        return
    category = service.get_category_by_id(category_id=req.category_id)
    if category is not None:
        _check(category["category"], None, protected)
