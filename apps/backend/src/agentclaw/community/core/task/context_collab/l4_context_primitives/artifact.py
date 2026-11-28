"""Closed inline artifact values; external paths remain subject to resource ACLs."""

from __future__ import annotations
from dataclasses import asdict, dataclass, fields
from urllib.parse import unquote, urlsplit
from pathlib import PurePosixPath, PureWindowsPath
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_values import (
    text,
    shape,
    MAX_CONTENT_BYTES,
)


@dataclass(frozen=True)
class ArtifactContent:
    """Only the three exact concrete content shapes may cross a boundary."""


@dataclass(frozen=True)
class ArtifactContentFile(ArtifactContent):
    storage_type: str
    storage_path: str
    file_name: str
    file_type: str
    file_content: str

    def __post_init__(self) -> None:
        for name in ("storage_type", "file_name", "file_type"):
            text(getattr(self, name), maximum=256)
        text(self.storage_path, empty=True, maximum=4096)
        text(self.file_content, empty=True)
        # A locator is not permission to read the local host filesystem.
        parsed = urlsplit(self.storage_path)
        if (
            self.storage_path.startswith(("/", "~", "file:"))
            or PureWindowsPath(self.storage_path).is_absolute()
            or ".." in PurePosixPath(unquote(parsed.path)).parts
            or parsed.username
            or parsed.password
        ):
            raise Invalid("unsafe artifact storage locator")
        if not self.file_content and not self.storage_path:
            raise Invalid("file requires content or an external locator")


@dataclass(frozen=True)
class ArtifactContentLink(ArtifactContent):
    link: str
    desc: str

    def __post_init__(self) -> None:
        text(self.link, maximum=4096)
        text(self.desc, empty=True)
        parsed = urlsplit(self.link)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise Invalid("artifact link must be an HTTP(S) URL without credentials")


@dataclass(frozen=True)
class ArtifactContentText(ArtifactContent):
    text: str

    def __post_init__(self) -> None:
        text(self.text)


CONTENT_TYPES = (ArtifactContentFile, ArtifactContentLink, ArtifactContentText)


def decode_content(data: dict) -> ArtifactContent:
    if type(data) is dict:
        for cls in CONTENT_TYPES:
            if set(data) == {f.name for f in fields(cls)}:
                return cls(**data)
    raise Invalid("unknown or mixed artifact content shape")


@dataclass(frozen=True)
class Artifact:
    type: str
    name: str
    content: ArtifactContent

    def __post_init__(self) -> None:
        text(self.type, maximum=256)
        text(self.name, maximum=256)
        if type(self.content) not in CONTENT_TYPES:
            raise Invalid("artifact content must have an installed exact shape")
        if len(str(asdict(self)).encode("utf-8")) > MAX_CONTENT_BYTES:
            raise Invalid("artifact exceeds byte limit")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Artifact:
        shape(data, {"type", "name", "content"})
        return cls(data["type"], data["name"], decode_content(data["content"]))
