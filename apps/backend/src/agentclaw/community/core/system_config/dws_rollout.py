"""DWS-only rollout policy. Missing or invalid configuration never grants access."""

from typing import Annotated, Literal
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

CATEGORY = "dws_hosting"
KEY = "rollout"


class DwsRollout(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["disabled", "allowlist", "all"]
    staff_ids: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128)]],
        Field(max_length=10000),
    ] = Field(default_factory=list)

    def allows(self, actor: str) -> bool:
        return bool(actor) and (
            self.mode == "all" or (self.mode == "allowlist" and actor in self.staff_ids)
        )


def is_allowed(store, actor: str, env: str) -> bool:
    try:
        value = store.get_config(category=CATEGORY, config_key=KEY, env=env)
        return DwsRollout.model_validate(value).allows(actor)
    except Exception:
        return False


def is_stop(operation: str, data: dict) -> bool:
    return (operation == "enabled" and data.get("enabled") is False) or (
        operation == "takeover" and data.get("taken_over") is True
    )


def is_dws_category(category: str) -> bool:
    # COSEC: include case/accent/width variants that DB collations may equate.
    normalized = unicodedata.normalize("NFKD", category).casefold().strip()
    return "".join(c for c in normalized if not unicodedata.combining(c)) == CATEGORY
