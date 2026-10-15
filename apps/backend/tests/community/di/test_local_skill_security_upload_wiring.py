"""The unified Local Skill upload service can resolve its security handoff."""

from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.skill_center.local_skill_security_upload_hook import (
    LocalSkillSecurityUploadHook,
)
from agentclaw.community.core.skill_center.services.local_skill_security_upload_observer import (
    NoopLocalSkillSecurityUploadHook,
)
from agentclaw.community.core.skill_center.services.local_skill_security_upload_observer import (
    LocalSkillSecurityUploadObserver,
)


def test_local_skill_security_observer_resolves_from_composition_root(
    test_injector,
) -> None:
    assert test_injector.get(LocalSkillUploadServiceProtocol) is not None
    assert isinstance(
        test_injector.get(LocalSkillSecurityUploadHook),
        NoopLocalSkillSecurityUploadHook,
    )
    assert isinstance(
        test_injector.get(LocalSkillSecurityUploadObserver),
        LocalSkillSecurityUploadObserver,
    )
