"""The public validation seam accepts a complete capability target."""

import pytest
import yaml
from ._schema_helpers import _accept, _reject, _caps


def test_wrapped_skill_sets_and_stable_references_are_accepted_without_rewriting_input():
    document = {
        "schema_version": 1,
        "manifest": {
            "capabilities": {
                "skills": [
                    {
                        "name": "review",
                        "origin": "repo",
                        "repo_path": "engineering/review",
                    }
                ],
                "skill_sets": [
                    {"name": "default"},
                    {
                        "name": "engineering",
                        "is_active": False,
                        "skills": [
                            {
                                "name": "research",
                                "origin": "center",
                                "skill_code": "public-research",
                            }
                        ],
                        "mcp": [{"server_code": "mcp.docs"}],
                    },
                ],
            }
        },
    }
    assert _accept(yaml.safe_dump(document)).parsed == document
    payload = _caps().as_payload()
    names = [row["name"] for row in payload["constructs"]]
    assert "capabilities" in names and "skills" not in names and "mcp" not in names
    assert [row["reference"] for row in payload["skill_references"]] == [
        "package",
        "repo_path",
        "skill_uuid",
        "skill_code",
    ]


@pytest.mark.parametrize(
    "manifest",
    [
        {"capabilities": {}, "skills": []},
        {"capabilities": None},
        {"capabilities": {"skills": None}},
        {"skill_sets": [{"name": "default", "is_active": True}]},
        {"skill_sets": [{"name": "engineering", "is_active": True, "skills": []}]},
        {"skills": [{"name": "review", "origin": "repo", "repo_path": "../review"}]},
        {"skills": [{"name": "review", "origin": "center", "skill_uuid": "bad"}]},
        {
            "skills": [
                {
                    "name": "review",
                    "origin": "center",
                    "skill_uuid": "5e79e624-6898-4d77-91fd-11b9fb0822c4",
                    "skill_code": "review",
                }
            ]
        },
    ],
)
def test_invalid_capability_shapes_are_refused(manifest):
    assert _reject(yaml.safe_dump({"schema_version": 1, "manifest": manifest}))


def test_inactive_members_share_the_direct_declaration_budget():
    manifest = {
        "skills": [{"name": "direct", "origin": "repo", "repo_path": "direct"}],
        "skill_sets": [
            {
                "name": "set",
                "is_active": False,
                "skills": [
                    {"name": f"skill{i}", "origin": "repo", "repo_path": f"skill{i}"}
                    for i in range(50)
                ],
                "mcp": [],
            }
        ],
    }
    violations = _reject(yaml.safe_dump({"schema_version": 1, "manifest": manifest}))
    assert any(code == "too_many_entries" for _, code in violations)


def test_center_references_follow_the_runtime_engine_support():
    caps = _caps(engine="deepseek_harness")
    assert caps.skill_reference("package").supported
    assert caps.skill_reference("repo_path").supported
    verdict = caps.skill_reference("skill_code")
    assert not verdict.supported and verdict.reason
    assert _reject(
        """schema_version: 1
manifest:
  capabilities:
    skills:
      - name: review
        origin: center
        skill_code: public-review
""",
        engine="deepseek_harness",
    )


def test_nested_local_source_placeholders_are_checked():
    assert _reject("""schema_version: 1
sources:
  packages: {protocol: git, url: 'https://example.com/repo'}
manifest:
  capabilities:
    skill_sets:
      - name: team
        is_active: true
        mcp: []
        skills:
          - name: review
            from: packages
            subpath: '${MISSPELLED}/review'
""")
