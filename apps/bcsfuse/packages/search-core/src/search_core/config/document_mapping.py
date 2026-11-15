"""Optional, bounded Card projection rules. Paths always start at business JSON."""

import re
from dataclasses import dataclass
from string import Formatter

from pydantic import BaseModel, ConfigDict, Field, field_validator

RESERVED_FILTERS = frozenset(
    {
        "resource_type",
        "index_key",
        "source_id",
        "source_version",
        "search_text",
        "rerank_text",
        "name",
        "content",
        "text_hash",
        "agent_id",
        "worker_id",
        "profile_id",
        "profile_key",
    }
)
PATH = re.compile(r"[\w-]+(?:\[\])?(?:\.[\w-]+(?:\[\])?)*", re.UNICODE)


def path_parts(path):
    if not isinstance(path, str) or len(path) > 256 or not PATH.fullmatch(path):
        raise ValueError(
            "expected a field path such as provider.name or skills[].tags[]"
        )
    return path.split(".")


def template_parts(template):
    parts = list(Formatter().parse(template))
    for _, path, spec, conversion in parts:
        if path is not None:
            path_parts(path)
            if spec or conversion:
                raise ValueError(
                    "format specifications and conversions are unsupported"
                )
    return parts


class DocumentMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    # None represents an inherited template. Explicit null fields are rejected.
    search_text_template: str | None = Field(
        default=None, min_length=1, max_length=8192
    )
    rerank_text_template: str | None = Field(
        default=None, min_length=1, max_length=8192
    )
    filter_fields: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("search_text_template", "rerank_text_template")
    @classmethod
    def validate_template(cls, value):
        if value is None or not value.strip():
            raise ValueError("template must be a nonblank string; omit it to inherit")
        template_parts(value)
        return value

    @field_validator("filter_fields")
    @classmethod
    def validate_filters(cls, value):
        for key, path in value.items():
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", key)
                or key in RESERVED_FILTERS
            ):
                raise ValueError(
                    f"filter_fields.{key}: reserved or invalid filter name"
                )
            try:
                path_parts(path)
            except ValueError as error:
                raise ValueError(f"filter_fields.{key}: {error}") from error
        return value

    def stored(self):
        return self.model_dump(exclude_none=True, exclude_defaults=True)


class MappingError(ValueError):
    def __init__(
        self, location, message, *, card_path="", code="INVALID_DOCUMENT_MAPPING"
    ):
        super().__init__(message)
        self.location = tuple(location)
        self.card_path = card_path
        self.code = code

    def detail(self):
        return {
            "loc": ["body", "document_mapping", *self.location],
            "code": self.code,
            "message": str(self),
            "card_path": self.card_path,
        }


def effective_mapping(global_mapping, override, *, validate_keys=False):
    base = global_mapping or DocumentMapping()
    local = DocumentMapping.model_validate(override or {})
    extra = local.filter_fields.keys() - base.filter_fields.keys()
    if validate_keys and extra:
        key = sorted(extra)[0]
        raise MappingError(
            ("filter_fields", key),
            "single-Card filters must be declared in the global mapping",
        )
    values = {**base.stored(), **local.stored()}
    values["filter_fields"] = {
        key: local.filter_fields.get(key, path)
        for key, path in base.filter_fields.items()
    }
    return DocumentMapping.model_validate(values)


def extract(card, path, location):
    values = [(card, "")]
    for segment in path_parts(path):
        array = segment.endswith("[]")
        key = segment[:-2] if array else segment
        following = []
        for value, parent in values:
            if value is None:
                continue
            if not isinstance(value, dict):
                raise MappingError(
                    location,
                    "expected an object while resolving field",
                    card_path=parent,
                )
            child = f"{parent}.{key}" if parent else key
            value = value.get(key)
            if value is None:
                continue
            if array:
                if not isinstance(value, list):
                    raise MappingError(
                        location,
                        "expected an array for [] expansion",
                        card_path=child,
                        code="EXPECTED_ARRAY",
                    )
                following.extend(
                    (item, f"{child}[{i}]") for i, item in enumerate(value)
                )
            else:
                following.append((value, child))
        values = following
    return [(value, path) for value, path in values if value is not None]


@dataclass(frozen=True)
class MappingResult:
    search_text: str
    rerank_text: str
    filters: dict[str, tuple[str, ...]]
    warnings: tuple[dict, ...]


def render(mapping, card, *, search_text="", rerank_text="", filters=None):
    warnings = []

    def values(path, location, strings_only=False):
        result = []
        for value, card_path in extract(card, path, location):
            if isinstance(value, str):
                text = value.strip()
            elif not strings_only and isinstance(value, (int, float, bool)):
                text = str(value).lower() if isinstance(value, bool) else str(value)
            else:
                raise MappingError(
                    location,
                    "expected a string"
                    if strings_only
                    else "expected a scalar; use [] to expand arrays",
                    card_path=card_path,
                )
            if text:
                result.append(text)
        if not result:
            warnings.append(
                {
                    "loc": ["document_mapping", *location],
                    "code": "PATH_HAS_NO_VALUE",
                    "path": path,
                    "message": "path has no nonblank value in this Card",
                }
            )
        return result

    def expand(template, location):
        chunks = []
        for literal, path, _, _ in template_parts(template):
            chunks.append(literal)
            if path is not None:
                chunks.append("\n".join(values(path, location)))
        text = "".join(chunks)
        if len(text) > 8192:
            raise MappingError(location, "expanded text exceeds 8192 characters")
        return text

    if mapping.search_text_template is not None:
        search_text = expand(mapping.search_text_template, ("search_text_template",))
    if not search_text.strip():
        raise MappingError(
            ("search_text_template",),
            "expanded search text is empty",
            code="EMPTY_SEARCH_TEXT",
        )
    if mapping.rerank_text_template is not None:
        rerank_text = expand(mapping.rerank_text_template, ("rerank_text_template",))
    output_filters = dict(filters or {})
    for key, path in mapping.filter_fields.items():
        found = tuple(
            dict.fromkeys(values(path, ("filter_fields", key), strings_only=True))
        )
        output_filters.pop(key, None)
        if found:
            output_filters[key] = found
    return MappingResult(
        search_text,
        rerank_text if rerank_text.strip() else search_text,
        output_filters,
        tuple(warnings),
    )
