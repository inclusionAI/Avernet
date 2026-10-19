"""Consumer conformance for the Local Skill post-success hook Service API."""

from threading import Event

from agentclaw.community.core.skill_center.local_skill_security_upload_hook import (
    LocalSkillSecurityUploadHook,
)
from agentclaw.community.core.skill_center.services.local_skill_security_upload_observer import (
    NoopLocalSkillSecurityUploadHook,
)


def test_public_noop_hook_accepts_exact_upload_and_timeout_facts(world):
    hook: LocalSkillSecurityUploadHook = world.get(LocalSkillSecurityUploadHook)
    assert isinstance(hook, NoopLocalSkillSecurityUploadHook)
    assert isinstance(hook, LocalSkillSecurityUploadHook)
    hook.observe(
        bot_id="bot",
        owner_id="owner",
        actor_id="uploader",
        skill_id=9,
        operation="created",
        name="skill",
        description="frozen",
        canonical_zip=b"exact package",
        observation_id="observation-a",
        incarnation_id="local-incarnation-9",
        cancelled=Event(),
    )
    hook.record_handoff_timeout(
        bot_id="bot",
        owner_id="owner",
        actor_id="uploader",
        skill_id=9,
        observation_id="observation-a",
        digest="sha256:known",
        incarnation_id="local-incarnation-9",
        name="skill",
        description="frozen",
        creator_id="uploader",
    )
    hook.record_deletion(
        bot_id="bot", owner_id="owner", actor_id="uploader", skill_id=9,
    )


def test_disabled_observation_does_not_read_security_identity():
    hook = NoopLocalSkillSecurityUploadHook()
    assert hook.current_incarnation_id(
        bot_id="bot", owner_id="owner", skill_id=9
    ) is None
