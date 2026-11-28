"""Storage-only mapping for MethodRelationTemplate's historical endpoint keys.

Authoring and v2 HTTP use source_method/target_method. Legacy v1 storage keeps
its canonical bytes/digests; v2 readers also accept old persisted compositions.
This adapter never changes L5 MethodCompositionEdge's binding wire contract.
"""

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


def to_legacy_relation_fields(value):
    return _rename(
        value, {"source_method": "parent_method", "target_method": "child_method"}
    )


def from_legacy_relation_fields(value):
    return _rename(
        value, {"parent_method": "source_method", "child_method": "target_method"}
    )


def _rename(value, names):
    if type(value) is list:
        return [_rename(item, names) for item in value]
    if type(value) is not dict:
        return value
    result = {key: _rename(item, names) for key, item in value.items()}
    if result.get("type") == "MethodRelationTemplate" and set(names) & result.keys():
        if set(result) != {"type", "relation", *names}:
            raise ContextCollabValidationError(
                "invalid or mixed MethodRelationTemplate storage fields"
            )
        return {names.get(key, key): item for key, item in result.items()}
    return result
