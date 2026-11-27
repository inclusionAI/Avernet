"""Directed Method endpoints, strict public names and historical storage reads."""

import json
from dataclasses import fields, replace

import pytest
from .helpers import persist_profile

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodRelationTemplate,
    MethodTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_models import (
    ProductProfileModel,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.profile_repository import (
    SqlProductProfileRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductConfigurationRow,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    decode_composition,
    decode_profile,
    encode_composition,
)
from agentclaw.community.core.task.context_collab.l6_products.profile_codec import (
    decode_profile as decode_legacy_profile,
    encode_profile as encode_legacy_profile,
    profile_digest,
)
from agentclaw.community.core.task.context_collab.l6_products.topology import (
    product_relation_id,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    OPEN_SINGLE_COMMISSION_PROFILE,
)


def _composition(runtime):
    return replace(
        runtime.composition,
        required_methods=(
            MethodTemplate(get_profile("Open")),
            *runtime.composition.required_methods,
        ),
        relations=(
            MethodRelationTemplate(
                source_method="Swarm",
                target_method="Open",
                relation=CompositionRelation.SUPPORT,
            ),
        ),
    )


@pytest.mark.parametrize("relation", list(CompositionRelation))
def test_relation_endpoints_are_named_source_and_target(relation):
    value = MethodRelationTemplate(
        source_method="Swarm", target_method="Single", relation=relation
    )
    assert [field.name for field in fields(value)] == [
        "source_method",
        "target_method",
        "relation",
    ]
    assert value.source_method == "Swarm" and value.target_method == "Single"
    assert value.relation is relation
    with pytest.raises(TypeError):
        MethodRelationTemplate(
            parent_method="Swarm", child_method="Single", relation=relation
        )


def test_composition_public_codec_uses_only_new_endpoint_names(runtime):
    composition = _composition(runtime)
    encoded = encode_composition(composition)
    assert encoded["relations"] == [
        {
            "type": "MethodRelationTemplate",
            "source_method": "Swarm",
            "target_method": "Open",
            "relation": "support",
        }
    ]
    assert decode_composition(encoded) == composition


@pytest.mark.parametrize("shape", ["legacy", "mixed", "partial", "unknown"])
def test_public_composition_rejects_legacy_or_ambiguous_fields(runtime, shape):
    encoded = encode_composition(_composition(runtime))
    relation = encoded["relations"][0]
    if shape in {"legacy", "partial"}:
        relation["parent_method"] = relation.pop("source_method")
    if shape == "legacy":
        relation["child_method"] = relation.pop("target_method")
    if shape == "mixed":
        relation.update(parent_method="Swarm", child_method="Open")
    if shape == "unknown":
        relation["unknown"] = "extra"
    with pytest.raises(Invalid):
        decode_composition(encoded)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_storage", [False, True])
async def test_sql_profile_and_runtime_preserve_relation_direction(
    runtime, legacy_storage
):
    h = runtime
    composition = _composition(h)
    profile = persist_profile(h, "composed", composition, h.stages)
    collet = await h.service.create_product_collet(profile.id, "request")
    with h.repository.db.orm_session() as session:
        row = session.query(ProductConfigurationRow).filter_by(id=profile.id).one()
        stored = json.loads(row.definition_json)
        relation = stored["composition"]["relations"][0]
        assert relation["source_method"] == "Swarm"
        assert relation["target_method"] == "Open"
        if legacy_storage:
            relation["parent_method"] = relation.pop("source_method")
            relation["child_method"] = relation.pop("target_method")
            row.definition_json = json.dumps(stored)
        original_json = row.definition_json
    repository = SqlProductRuntimeRepository(h.repository.db)
    assert repository.get_profile(profile.id)[0] == profile
    snapshot = repository.get_collet(collet.id)
    assert len(snapshot.relations) == 1
    edge = snapshot.relations[0]
    by_id = {
        binding.method_binding_id: binding.method_kind for binding in snapshot.bindings
    }
    assert (by_id[edge.parent_binding], by_id[edge.child_binding]) == ("Swarm", "Open")
    assert edge.relation_kind is CompositionRelation.SUPPORT
    assert not h.calls
    with h.repository.db.orm_session() as session:
        row = session.query(ProductConfigurationRow).filter_by(id=profile.id).one()
        assert (
            row.definition_json == original_json
        )  # Reads never rewrite old definitions.


@pytest.mark.parametrize("shape", ["mixed", "partial", "unknown"])
def test_persisted_composition_rejects_ambiguous_legacy_fields(runtime, shape):
    data = {
        "id": 1,
        "name": "composed",
        "composition": encode_composition(_composition(runtime)),
        "stages": runtime.stages,
    }
    relation = data["composition"]["relations"][0]
    relation["parent_method"] = relation.pop("source_method")
    if shape != "partial":
        relation["child_method"] = relation.pop("target_method")
    if shape == "mixed":
        relation["source_method"] = "Single"
    if shape == "unknown":
        relation["unknown"] = "extra"
    with pytest.raises(Invalid):
        decode_profile(data)


def test_legacy_v1_definition_bytes_digest_and_edge_id_are_unchanged(db):
    profile = OPEN_SINGLE_COMMISSION_PROFILE
    encoded = encode_legacy_profile(profile)
    relation = json.loads(encoded)["definition"]["composition"]["relations"][0]
    assert relation == {
        "type": "MethodRelationTemplate",
        "parent_method": "Single",
        "child_method": "Open",
        "relation": "support",
    }
    assert decode_legacy_profile(encoded) == profile
    # Captured from unchanged HEAD 3857a08 before the Python endpoint rename.
    digest = "d452bf777131b52d39d218c5c0ba95a80953c3febc87f70342c540eaecae6f6b"
    assert profile_digest(profile) == digest
    assert product_relation_id("collet", profile.composition.relations[0]) == (
        "mce_collet_single_support_open"
    )
    assert SqlProductProfileRepository(db).get("task_commission", 2) == profile
    with db.orm_session() as session:
        row = (
            session.query(ProductProfileModel)
            .filter_by(profile_id="task_commission", profile_version=2)
            .one()
        )
        assert row.definition_json == encoded and row.definition_digest == digest


def test_legacy_v1_schema_still_rejects_noncanonical_new_wire_names():
    data = json.loads(encode_legacy_profile(OPEN_SINGLE_COMMISSION_PROFILE))
    relation = data["definition"]["composition"]["relations"][0]
    relation["source_method"] = relation.pop("parent_method")
    relation["target_method"] = relation.pop("child_method")
    with pytest.raises(Invalid, match="noncanonical"):
        decode_legacy_profile(json.dumps(data))
