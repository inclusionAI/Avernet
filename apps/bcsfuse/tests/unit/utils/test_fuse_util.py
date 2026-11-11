"""G9 cache keys follow the current TC model input rather than archived MD."""

import pytest

from app.domain.models.profile_fusion import FusionContext
from app.domain.models.worker_profile_content import SkillSet, WorkerProfileContent
from app.utils.fuse_util import (
    calculate_profile_content_hash,
    generate_fusion_id,
    worker_profile_to_dict,
)


def profile():
    return WorkerProfileContent(
        worker_id="tc:database:owner", display_name="Database specialist",
        description="Review database migrations", contents={"profile": "Staged release expertise"},
        skill_sets=[SkillSet(name="sql", description="Review transactional changes")],
    )


@pytest.mark.parametrize("field", ["display_name", "description", "profile", "skill_description"])
def test_current_model_input_changes_invalidate_content_hash(field):
    original = profile()
    changed = original.model_copy(deep=True)
    if field == "profile":
        changed.contents["profile"] = "New recovery expertise"
    elif field == "skill_description":
        changed.skill_sets[0].description = "Review schema rollbacks"
    else:
        setattr(changed, field, "Changed current input")
    assert calculate_profile_content_hash(worker_profile_to_dict(changed)) != (
        calculate_profile_content_hash(worker_profile_to_dict(original))
    )


def test_archived_md_and_skill_implementation_do_not_change_current_cache_key():
    original = profile()
    changed = original.model_copy(deep=True)
    changed.soul_md = "Archived soul"
    changed.contents.update({"identity.md": "Archived identity", "memory.md": "Archived memory"})
    changed.skill_sets[0].content = "Private skill implementation"
    assert calculate_profile_content_hash(worker_profile_to_dict(changed)) == (
        calculate_profile_content_hash(worker_profile_to_dict(original))
    )


def test_cache_key_keeps_worker_to_input_association():
    first = profile()
    second = first.model_copy(update={"worker_id": "another:owner", "description": "Risk analysis"})

    def fusion_id(profiles):
        return generate_fusion_id("bot_profile_fuse", FusionContext(
            group_id="group", participant_ids=[p.worker_id for p in profiles],
            profiles_dict=[worker_profile_to_dict(p) for p in profiles],
        ))

    assert fusion_id([first, second]) == fusion_id([second, first])
    swapped_first = first.model_copy(update={"description": second.description})
    swapped_second = second.model_copy(update={"description": first.description})
    assert fusion_id([first, second]) != fusion_id([swapped_first, swapped_second])


def test_skill_order_does_not_change_cache_key():
    original = profile()
    original.skill_sets.append(SkillSet(name="python", description="Test release scripts"))
    reversed_skills = original.model_copy(update={"skill_sets": list(reversed(original.skill_sets))})
    assert calculate_profile_content_hash(worker_profile_to_dict(original)) == (
        calculate_profile_content_hash(worker_profile_to_dict(reversed_skills))
    )
