"""Engine publish-ignore application service contract."""
from typing import Protocol, runtime_checkable
from engine.community.core.publish_ignore.models import PublishIgnoreQuery, PublishIgnoreRequest


@runtime_checkable
class PublishIgnoreService(Protocol):
    def change(self, request: PublishIgnoreRequest) -> dict: ...

    def query(self, request: PublishIgnoreQuery) -> dict:
        """Read one identity-checked snapshot without creating runtime files."""
        ...
