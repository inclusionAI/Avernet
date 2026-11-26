"""Open Method profile contracts; fixtures in open_profile_support."""

from dataclasses import replace

import pytest
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    AdmissionDecision,
    ApplicationState,
    OpenOpportunity,
    OpportunityState,
    ParticipationApplication,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)

from tests.community.core.task.context_collab.open_profile_support import (
    APPLICATION_MACHINE,
    AXES,
    COLLET_ID,
    OPPORTUNITY_MACHINE,
    application_dict,
    decision_dict,
    opportunity_dict,
)


class TestOpenValueObjects:
    def test_round_trips(self):
        for build, owner in (
            (opportunity_dict, OpenOpportunity),
            (application_dict, ParticipationApplication),
            (decision_dict, AdmissionDecision),
        ):
            obj = owner.from_dict(build())
            assert owner.from_dict(obj.to_dict()) == obj

    def test_state_wire_validation(self):
        assert OpportunityState.from_wire("PAUSED") is OpportunityState.PAUSED
        assert ApplicationState.from_wire("WITHDRAWN") is ApplicationState.WITHDRAWN
        for enum_cls in (OpportunityState, ApplicationState):
            with pytest.raises(ContextCollabValidationError, match="wire"):
                enum_cls.from_wire("BOUND")

    def test_state_vocabularies_match_registered_machines(self):
        """防漂移:两个状态枚举与 G4 注册机器逐字一致。"""
        for enum_cls, machine in (
            (OpportunityState, OPPORTUNITY_MACHINE),
            (ApplicationState, APPLICATION_MACHINE),
        ):
            assert {s.value for s in enum_cls} == {s.name for s in machine.states}

    def test_participation_type_closed_vocab(self):
        assert (
            ParticipationType.from_wire("SWARM_ALLOCATION")
            is ParticipationType.SWARM_ALLOCATION
        )
        with pytest.raises(ContextCollabValidationError, match="participation type"):
            ParticipationType.from_wire("TEAM_MEMBER")

    def test_unknown_field_rejected(self):
        for build, owner in (
            (opportunity_dict, OpenOpportunity),
            (application_dict, ParticipationApplication),
            (decision_dict, AdmissionDecision),
        ):
            with pytest.raises(ContextCollabValidationError, match="unknown field"):
                owner.from_dict({**build(), "extra": 1})

    def test_required_fields_non_empty(self):
        for field in ("opportunity_id", "method_binding_id", "scope_ref", "opens_at"):
            with pytest.raises(ContextCollabValidationError):
                OpenOpportunity.from_dict(opportunity_dict(**{field: ""}))
        for field in (
            "application_id",
            "opportunity_ref",
            "applicant_agent_ref",
            "accountable_principal_ref",
            "submitted_at",
        ):
            with pytest.raises(ContextCollabValidationError):
                ParticipationApplication.from_dict(application_dict(**{field: ""}))
        for field in ("admission_decision_id", "application_ref", "decision_ref"):
            with pytest.raises(ContextCollabValidationError):
                AdmissionDecision.from_dict(decision_dict(**{field: ""}))

    def test_capacity_positive_int_or_none(self):
        assert OpenOpportunity.from_dict(opportunity_dict()).capacity is None
        assert OpenOpportunity.from_dict(opportunity_dict(capacity=5)).capacity == 5
        for bad in (0, -1, "3", 3.5, True):
            with pytest.raises(ContextCollabValidationError, match="capacity"):
                OpenOpportunity.from_dict(opportunity_dict(capacity=bad))

    def test_optional_and_ref_fields(self):
        assert (
            OpenOpportunity.from_dict(
                opportunity_dict(closes_at="2099-01-01T00:00:00+00:00")
            ).closes_at
            == "2099-01-01T00:00:00+00:00"
        )
        with pytest.raises(ContextCollabValidationError, match="closes_at"):
            OpenOpportunity.from_dict(opportunity_dict(closes_at=""))
        with pytest.raises(ContextCollabValidationError, match="requirement_refs"):
            OpenOpportunity.from_dict(opportunity_dict(requirement_refs=[""]))
        decision = AdmissionDecision.from_dict(
            decision_dict(admitted_scope_ref=None, resulting_binding_refs=[])
        )
        assert decision.admitted_scope_ref is None
        assert decision.resulting_binding_refs == ()


class TestOpenStoreSlice:
    def test_opportunity_idempotent_insert_and_shape_conflict(self):
        store = InMemoryMethodStore()
        opportunity = OpenOpportunity.from_dict(opportunity_dict())
        assert store.insert_opportunity(AXES, opportunity, COLLET_ID) is opportunity
        assert store.insert_opportunity(AXES, opportunity, COLLET_ID) is opportunity
        with pytest.raises(ContextCollabValidationError, match="stored shape"):
            store.insert_opportunity(
                AXES,
                OpenOpportunity.from_dict(opportunity_dict(capacity=3)),
                COLLET_ID,
            )
        updated = replace(
            opportunity,
            state=OpportunityState.from_wire("PAUSED"),
            transitioned_from="OPEN",
        )
        store.update_opportunity_state(AXES, updated, COLLET_ID)
        with pytest.raises(ContextCollabNotFoundError):
            store.update_opportunity_state(
                AXES,
                OpenOpportunity.from_dict(opportunity_dict(opportunity_id="opp_ghost")),
                COLLET_ID,
            )

    def test_application_unique_active_applicant_and_reentry(self):
        """存储层唯一断言:同 (机会, 申请人) 非终态唯一;终态后可再投。"""
        store = InMemoryMethodStore()
        application = store.insert_application(
            AXES,
            "mb_open_001",
            ParticipationApplication.from_dict(application_dict()),
            COLLET_ID,
        )
        assert application.state is ApplicationState.SUBMITTED
        with pytest.raises(
            ContextCollabValidationError, match="non-terminal application"
        ):
            store.insert_application(
                AXES,
                "mb_open_001",
                ParticipationApplication.from_dict(
                    application_dict(application_id="app_002")
                ),
                COLLET_ID,
            )
        # 同申请人在别的机会/别的 binding 不占用。
        store.insert_application(
            AXES,
            "mb_open_001",
            ParticipationApplication.from_dict(
                application_dict(application_id="app_003", opportunity_ref="opp_002")
            ),
            COLLET_ID,
        )
        withdrawn = replace(application, state=ApplicationState.from_wire("WITHDRAWN"))
        store.update_application_state(AXES, "mb_open_001", withdrawn, COLLET_ID)
        revived = store.insert_application(
            AXES,
            "mb_open_001",
            ParticipationApplication.from_dict(
                application_dict(application_id="app_004")
            ),
            COLLET_ID,
        )
        assert revived.state is ApplicationState.SUBMITTED
        with pytest.raises(ContextCollabNotFoundError):
            store.update_application_state(
                AXES,
                "mb_open_001",
                ParticipationApplication.from_dict(
                    application_dict(application_id="app_ghost")
                ),
                COLLET_ID,
            )

    def test_decision_insert_idempotent_and_scoped(self):
        store = InMemoryMethodStore()
        decision = AdmissionDecision.from_dict(decision_dict())
        assert (
            store.insert_decision(AXES, "mb_open_001", decision, COLLET_ID) is decision
        )
        assert store.get_decision(AXES, "dec_001", "mb_open_001", COLLET_ID) is decision
        assert store.get_decision(AXES, "dec_001", "mb_open_002", COLLET_ID) is None
        assert [
            d.admission_decision_id
            for d in store.list_decisions(AXES, "mb_open_001", COLLET_ID)
        ] == ["dec_001"]
