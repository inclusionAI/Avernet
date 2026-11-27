from dataclasses import replace
from hashlib import sha256

import pytest
from sqlalchemy import inspect

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError as Missing,
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfileStatus as P,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_models import (
    ProductProfileRow,
    ColletRow,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_profile,
    encode_profile_response,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.profile_config_yaml import (
    decode_config,
    encode_config,
    MAX_CONFIG_BYTES,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.snapshot_codec import (
    canonical,
)
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope


def test_only_five_tables_and_no_snapshot_dependencies(business):
    assert set(inspect(business.engine).get_table_names()) == {
        "ac_context_collab_product_profile",
        "ac_context_collab_collet",
        "ac_context_collab_collet_rollout",
        "ac_context_collab_stage_rollout",
        "ac_context_collab_event",
    }
    indexes = inspect(business.engine).get_indexes(ProductProfileRow.__tablename__)
    assert [(i["name"], i["column_names"], bool(i["unique"])) for i in indexes] == [
        ("idx_cc_profile_scope_name", ["avernet_tenant", "env", "name"], False)
    ]


def test_create_roundtrip_audit_and_independent_same_name_invocations(business):
    h = business
    first = h.repo.create_profile(
        "coding", h.composition, h.stages, h.owner, description="中文"
    )
    second = h.repo.create_profile("coding", h.composition, h.stages, h.owner)
    assert first.id != second.id
    assert first == h.repo.get_profile(first.id)
    assert first.status is P.DRAFT
    assert first.creator == first.modifer == "human:creator"
    assert first.description == "中文"
    with h.db.orm_session() as session:
        row = session.get(ProductProfileRow, first.id)
        assert decode_config(row.config) == (h.composition, h.stages)
        assert "creator:" not in row.config and "status:" not in row.config
    metadata = encode_profile_response(first)
    assert metadata["modifer"] == "human:creator" and metadata["status"] == "DRAFT"
    assert set(encode_profile(first.definition)) == {
        "id",
        "name",
        "composition",
        "stages",
    }
    before = sha256(canonical(encode_profile(first.definition)).encode()).hexdigest()
    changed = replace(
        first, status=P.OFFLINE, description="other", modifer="human:reviewer"
    )
    assert (
        sha256(canonical(encode_profile(changed.definition)).encode()).hexdigest()
        == before
    )


@pytest.mark.parametrize("value", ["ONLINE", "OFFLINE", "DRAFT"])
def test_lifecycle_read_exact_and_metadata_not_execution(business, value):
    h = business
    profile = h.repo.create_profile("p", h.composition, h.stages, h.owner)
    with h.db.orm_session() as session:
        session.query(ProductProfileRow).filter_by(id=profile.id).update(
            {"status": value}
        )
    assert h.repo.get_profile(profile.id).status is P(value)


@pytest.mark.parametrize("value", ["", "draft", "SUCCEEDED", " ONLINE"])
def test_invalid_lifecycle_not_defaulted(business, value):
    h = business
    profile = h.repo.create_profile("p", h.composition, h.stages, h.owner)
    with h.db.orm_session() as session:
        session.query(ProductProfileRow).filter_by(id=profile.id).update(
            {"status": value}
        )
    with pytest.raises(Invalid, match="lifecycle"):
        h.repo.get_profile(profile.id)


@pytest.mark.parametrize("description", [None, 1, "x" * 2049])
def test_invalid_description_no_write(business, description):
    h = business
    with pytest.raises(Invalid):
        h.repo.create_profile(
            "p", h.composition, h.stages, h.owner, description=description
        )
    with h.db.orm_session() as session:
        assert session.query(ProductProfileRow).count() == 0


def test_publication_and_use_authorities_are_independent(online):
    h = online
    stranger = ActorContext(PrincipalRef("human", "user"), "test")
    with pytest.raises(Invalid, match="publication"):
        h.repo.create_profile("p", h.composition, h.stages, stranger)
    with pytest.raises(Forbidden, match="not configured"):
        h.denied.create_collet(h.profile.id, "work", h.owner)
    history = h.repo.create_collet(h.profile.id, "work", stranger)
    assert h.grant.calls[-1] == (h.profile.id, stranger)
    assert history.owner == stranger.actor
    assert h.repo.get_profile(h.profile.id).creator == "human:creator"
    with pytest.raises(Forbidden):
        h.repo.get_collet(history.collet.id, h.owner)


@pytest.mark.parametrize("value", ["DRAFT", "OFFLINE"])
def test_unavailable_profile_rolls_back_without_business_rows(online, value):
    h = online
    with h.db.orm_session() as session:
        session.query(ProductProfileRow).filter_by(id=h.profile.id).update(
            {"status": value}
        )
    with pytest.raises(Invalid, match="ONLINE"):
        h.repo.create_collet(h.profile.id, "work", h.owner)
    with h.db.orm_session() as session:
        assert session.query(ColletRow).count() == 0


def test_scope_is_trusted_and_unknown_audit_honest(online):
    h = online
    with avernet_tenant_scope("unrelated-tenant"):
        with pytest.raises(Missing):
            h.repo.get_profile(h.profile.id)
    with h.db.orm_session() as session:
        session.query(ProductProfileRow).filter_by(id=h.profile.id).update(
            {"creator": None, "modifer": None}
        )
    assert h.repo.get_profile(h.profile.id).creator is None
    assert h.repo.get_profile(h.profile.id).modifer is None


@pytest.mark.parametrize(
    "actor", [PrincipalRef("hu:man", "x"), PrincipalRef("human", "x" * 1020)]
)
def test_audit_encoding_rejects_ambiguous_or_oversize_identity(business, actor):
    h = business
    # Use an explicitly authorized publisher to isolate audit validation.
    from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
        ProfilePublisherAllowlist,
    )
    from agentclaw.community.core.task.context_collab.l6_products.persistence.product_relational_repository import (
        SqlProductBusinessRepository,
    )

    repository = SqlProductBusinessRepository(
        h.db, ProfilePublisherAllowlist((actor,)), h.grant, h.repo.authorization
    )
    with pytest.raises(Invalid, match="audit"):
        repository.create_profile(
            "p", h.composition, h.stages, ActorContext(actor, "test")
        )


def test_yaml_preserves_original_bytes_and_rejects_unsupported_forms(business):
    h = business
    stages = h.stages.replace("stages:\n", "# comment\r\nstages:\n")
    wire = encode_config(h.composition, stages)
    assert decode_config(wire) == (h.composition, stages)
    for bad in [
        wire + "unknown: true\n",
        wire.replace("product-profile/config@1", "bad@2"),
        wire + "schema: duplicate\n",
        "schema: &s product-profile/config@1\ncomposition: *s\nstages: x\n",
        "schema: !!python/object:evil {}",
        "x" * (MAX_CONFIG_BYTES + 1),
    ]:
        with pytest.raises(Invalid):
            decode_config(bad)


@pytest.mark.parametrize("filler", ["x", "字"])
def test_profile_config_text_utf8_byte_boundary_and_rejected_write(business, filler):
    h = business
    assert MAX_CONFIG_BYTES == 65535
    # Start with a long comment so PyYAML uses the same wrapping style for
    # both the baseline and the final boundary-sized serialized document.
    prefix = filler * 128
    base = encode_config(h.composition, "#" + prefix + "\n" + h.stages)
    room = MAX_CONFIG_BYTES - len(base.encode("utf-8"))
    width = len(filler.encode("utf-8"))
    padding = prefix + filler * (room // width) + "x" * (room % width)
    stages = "#" + padding + "\n" + h.stages
    wire = encode_config(h.composition, stages)
    assert len(wire.encode("utf-8")) == 65535
    assert decode_config(wire) == (h.composition, stages)

    profile = h.repo.create_profile("max-size", h.composition, stages, h.owner)
    assert h.repo.get_profile(profile.id).definition.stages == stages
    with h.db.orm_session() as session:
        assert len(session.get(ProductProfileRow, profile.id).config.encode()) == 65535

    with pytest.raises(Invalid, match="byte limit"):
        h.repo.create_profile("too-large", h.composition, "#x" + stages[1:], h.owner)
    with h.db.orm_session() as session:
        assert session.query(ProductProfileRow).count() == 1
    with pytest.raises(Invalid, match="byte limit"):
        decode_config(wire + "#")

    # SQLite itself does not enforce MySQL TEXT capacity. Readers must still
    # reject oversize data inserted outside the repository instead of hiding it.
    with h.db.orm_session() as session:
        session.get(ProductProfileRow, profile.id).config = wire + "#"
    with pytest.raises(Invalid, match="byte limit"):
        h.repo.get_profile(profile.id)
