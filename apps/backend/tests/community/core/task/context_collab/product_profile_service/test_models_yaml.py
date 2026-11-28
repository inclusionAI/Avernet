from dataclasses import fields, replace
import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ArtifactContent,
    ArtifactContentFile,
    ArtifactContentLink,
    ArtifactContentText,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
    EventTypeEnum as E,
    AdjustTypeEnum as A,
)
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfile,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
    MethodRelationTemplate,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    InteractiveResult,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.stages_yaml import (
    compile_stages,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    validate_configuration,
    encode_composition,
    decode_composition,
)


@pytest.mark.parametrize(
    "cls,names",
    [
        (ProductProfile, "id name composition stages"),
        (Collet, "id profile_id content status"),
        (Artifact, "type name content"),
        (ArtifactContent, ""),
        (
            ArtifactContentFile,
            "storage_type storage_path file_name file_type file_content",
        ),
        (ArtifactContentLink, "link desc"),
        (ArtifactContentText, "text"),
        (AcceptanceResult, "condition passed reason"),
        (ProgressEvent, "type stage_rollout_id artifacts acceptances error_msg"),
        (
            StageRollout,
            "name stage_rollout_id status assignee_type assignee_uuid input session_id artifacts acceptances error_msg",
        ),
        (ColletRollout, "collet_id rollout_id stages content status"),
        (InteractiveResult, "type message"),
    ],
)
def test_exact_domain_fields(cls, names):
    assert [f.name for f in fields(cls)] == names.split()


def test_enum_values_are_exact():
    assert {v.value for v in S} == {
        "CREATE",
        "RUNNING",
        "RUN_FINISHED",
        "RUN_FAILED",
        "ACCEPTANCE_SUCCEED",
        "ACCEPTANCE_FAILED",
    }
    assert {v.value for v in E} == {
        "RUN_FAILED",
        "ACCEPTANCE_SUCCEED",
        "ACCEPTANCE_FAILED",
    }
    assert {v.value for v in A} == {"DO_NOTHING", "OPTIMIZE", "RE_DO", "SHUTDOWN"}


@pytest.mark.parametrize("identity", [True, False, 0, -1, "1", 2**63])
def test_identity_is_strict(runtime, identity):
    with pytest.raises(Invalid):
        ProductProfile(identity, "name", runtime.composition, runtime.stages)
    with pytest.raises(Invalid):
        Collet(identity, 1, "request", S.CREATE)
    with pytest.raises(Invalid):
        ProgressEvent(E.RUN_FAILED, identity, [], [], "failed")


@pytest.mark.parametrize(
    "content",
    [
        ArtifactContentFile(
            "object", "bucket/file.txt", "file.txt", "text/plain", "body"
        ),
        ArtifactContentFile("object", "bucket/file.txt", "file.txt", "text/plain", ""),
        ArtifactContentLink("https://example.org/pr/42", "a PR"),
        ArtifactContentText("complete text"),
    ],
)
def test_artifact_closed_shape_roundtrip(content):
    artifact = Artifact("implementation", "result", content)
    assert Artifact.from_dict(artifact.to_dict()) == artifact
    assert set(artifact.to_dict()["content"]) == {f.name for f in fields(content)}


@pytest.mark.parametrize(
    "content",
    [
        {"text": "body", "link": "https://example.org"},
        {"type": "text", "text": "body"},
        {"unknown": "body"},
        {"link": "javascript:alert(1)", "desc": "bad"},
        {"link": "https://user:password@example.org", "desc": "bad"},
        {"text": 1},
    ],
)
def test_artifact_mixed_or_unknown_rejected(content):
    with pytest.raises(Invalid):
        Artifact.from_dict({"type": "doc", "name": "result", "content": content})


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../private", "bucket/../private", "file:///etc/passwd", "~/key"],
)
def test_file_locator_cannot_read_host(path):
    with pytest.raises(Invalid):
        ArtifactContentFile("object", path, "file", "text", "")


@pytest.mark.parametrize("passed", ["true", 1, None])
def test_acceptance_boolean_is_not_coerced(passed):
    with pytest.raises(Invalid):
        AcceptanceResult("condition", passed, "reason")


def test_mapping_and_list_conditions_compile_identically(runtime):
    specs = compile_stages(runtime.stages)
    assert specs[0].conditions == ("design is complete",)
    assert specs[1].conditions == ("tests pass",)
    assert specs[1].depends_on == ("analysis",)
    assert (
        decode_composition(encode_composition(runtime.composition))
        == runtime.composition
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("stages:", "stages: []\nstages:"),
        lambda s: s.replace("name: analysis", "name: analysis\n    name: duplicate"),
        lambda s: s.replace("name: implementation", "name: analysis"),
        lambda s: s.replace("depends_on: []", "depends_on: [implementation]"),
        lambda s: s.replace("depends_on: [analysis]", "depends_on: [missing]"),
        lambda s: s.replace("depends_on: [analysis]", "depends_on: [implementation]"),
        lambda s: s.replace("depends_on: []", "depends_on: false"),
        lambda s: s.replace("acceptance:", "extra: bad\n    acceptance:", 1),
        lambda s: s.replace("uuid: worker-a", "uuid: worker-a\n        extra: bad", 1),
        lambda s: s.replace("condition: design is complete", "condition: true"),
        lambda s: s.replace("condition: design is complete", "condition: !Custom text"),
        lambda s: "stages: &stages [*stages]",
        lambda s: "stages: !!python/object/apply:os.system ['not executed']",
        lambda s: "stages: []",
        lambda s: "stages: [" + "[" * 30 + "]" * 30 + "]",
    ],
)
def test_strict_yaml_rejection(runtime, mutate):
    with pytest.raises(Invalid):
        compile_stages(mutate(runtime.stages))


def test_composition_is_required_and_policy_not_implicitly_enabled(runtime):
    h = runtime
    for composition in [
        None,
        MethodCompositionTemplate(()),
        replace(
            h.composition, required_methods=(MethodTemplate(get_profile("Swarm")),)
        ),
        MethodCompositionTemplate((), h.composition.required_methods),
    ]:
        with pytest.raises(Invalid):
            validate_configuration(composition, h.stages, h.execution)
    invalid = replace(
        h.composition,
        required_methods=(
            MethodTemplate(get_profile("Open"), SwarmMethodPolicy("planner")),
        ),
    )
    with pytest.raises(Invalid):
        validate_configuration(invalid, h.stages, h.execution)


def test_optional_method_is_retained_but_not_selected(runtime):
    h = runtime
    composition = replace(
        h.composition, optional_methods=(MethodTemplate(get_profile("Single")),)
    )
    specs, policy = validate_configuration(composition, h.stages, h.execution)
    assert policy == h.composition.required_methods[0].policy
    assert len(specs) == 2
    assert decode_composition(encode_composition(composition)) == composition


def test_invalid_relation_cannot_reference_optional_method(runtime):
    h = runtime
    relation = MethodRelationTemplate("Swarm", "Single", CompositionRelation.SUPPORT)
    composition = replace(
        h.composition,
        optional_methods=(MethodTemplate(get_profile("Single")),),
        relations=(relation,),
    )
    with pytest.raises(Invalid):
        validate_configuration(composition, h.stages, h.execution)


def test_capability_rejects_unsupported_candidate_type_at_profile_creation(runtime):
    with pytest.raises(Invalid, match="non-Bot"):
        validate_configuration(
            runtime.composition,
            runtime.stages.replace("type: bot", "type: human"),
            runtime.execution,
        )


def test_yaml_parser_error_does_not_expose_source_in_exception_chain(runtime):
    with pytest.raises(Invalid) as error:
        compile_stages("stages: [a: private-inline-content:")
    assert error.value.__suppress_context__ is True
    assert "private-inline-content" not in str(error.value)


def test_progress_invalid_enum_suppresses_private_source_exception():
    with pytest.raises(Invalid) as error:
        ProgressEvent.from_dict(
            {
                "type": "private-inline-content",
                "stage_rollout_id": 1,
                "artifacts": [],
                "acceptances": [],
                "error_msg": "",
            }
        )
    assert error.value.__suppress_context__ is True
    assert "private-inline-content" not in str(error.value)


@pytest.mark.parametrize("name", ["artifacts", "acceptances"])
def test_progress_rejects_oversized_evidence_before_decoding_entries(name):
    data = {
        "type": "RUN_FAILED",
        "stage_rollout_id": 1,
        "artifacts": [],
        "acceptances": [],
        "error_msg": "failed",
    }
    data[name] = [None] * 101
    with pytest.raises(Invalid, match="bounded lists"):
        ProgressEvent.from_dict(data)


def test_mysql_migration_ddl_matches_models_and_pins_transactional_utf8_defaults():
    import re
    from pathlib import Path
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateIndex, CreateTable
    from agentclaw.community.core.task.context_collab.l6_products.persistence import (
        rollout_models,
    )

    path = (
        Path(rollout_models.__file__).parents[2]
        / "sql/2026_11_26_product_runtime_v2.sql"
    )
    actual = "\n".join(
        line for line in path.read_text().splitlines() if not line.startswith("--")
    )
    generated = []
    for model in (
        rollout_models.ProductConfigurationRow,
        rollout_models.ProductIdentityRow,
        rollout_models.ProductRuntimeRow,
        rollout_models.ProductReceiptRow,
        rollout_models.ProductIntentRow,
        rollout_models.ProductCandidateSlotRow,
    ):
        ddl = str(CreateTable(model.__table__).compile(dialect=mysql.dialect())).strip()
        generated.append(
            ddl + " ENGINE=InnoDB DEFAULT CHARACTER SET utf8mb4 COLLATE=utf8mb4_bin;"
        )
        generated.extend(
            str(CreateIndex(index).compile(dialect=mysql.dialect())) + ";"
            for index in sorted(model.__table__.indexes, key=lambda index: index.name)
        )
    assert re.sub(r"\s+", "", actual) == re.sub(r"\s+", "", "\n".join(generated))
