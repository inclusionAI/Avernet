"""Stored Profile metadata is separate from the frozen four-field definition.

Legacy snapshot readers do not synthesize lifecycle/audit values that were never
stored. This record is returned only by the new relational business repository.
"""

from dataclasses import dataclass
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from .product_profile import ProductProfile, ProductProfileStatus


@dataclass(frozen=True)
class ProductProfileRecord:
    definition: ProductProfile
    description: str = ""
    status: ProductProfileStatus = ProductProfileStatus.DRAFT
    creator: str | None = None
    modifer: str | None = None

    def __post_init__(self):
        if type(self.definition) is not ProductProfile:
            raise Invalid("Profile record requires a frozen definition")
        if type(self.description) is not str or len(self.description) > 2048:
            raise Invalid("description exceeds 2048 characters")
        if type(self.status) is not ProductProfileStatus:
            raise Invalid("invalid Profile lifecycle status")
        for audit in (self.creator, self.modifer):
            if audit is not None and (
                type(audit) is not str
                or not audit.strip()
                or len(audit) > 1024
                or ":" not in audit
                or not all(audit.split(":", 1))
            ):
                raise Invalid("invalid Profile audit identity")

    @property
    def id(self) -> int:
        return self.definition.id
