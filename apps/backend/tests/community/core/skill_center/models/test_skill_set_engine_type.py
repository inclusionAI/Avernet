"""Ensure ORM models expose engine_type columns for SkillSet and UserDefaultSkillSet."""
from agentclaw.community.core.models.skill import SkillSet, UserDefaultSkillSet


def test_skill_set_has_engine_type_column():
    assert "engine_type" in SkillSet.__table__.columns
    col = SkillSet.__table__.columns["engine_type"]
    assert col.nullable is True
    assert col.type.length == 32


def test_user_default_skill_set_has_engine_type_column():
    assert "engine_type" in UserDefaultSkillSet.__table__.columns
    col = UserDefaultSkillSet.__table__.columns["engine_type"]
    assert col.nullable is True
    assert col.type.length == 32


def test_user_default_skill_set_unique_constraint_includes_engine_type():
    names = [
        tuple(sorted(c.name for c in uc.columns))
        for uc in UserDefaultSkillSet.__table__.constraints
        if uc.__class__.__name__ == "UniqueConstraint"
    ]
    assert ("bolt_id", "engine_type", "env", "user_id") in names


def test_skill_set_to_dict_includes_engine_type():
    s = SkillSet(name="x", bolt_id="b1", engine_type="aicoding")
    d = s.to_dict()
    assert d["engine_type"] == "aicoding"
