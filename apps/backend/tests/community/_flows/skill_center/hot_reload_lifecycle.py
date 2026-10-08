"""Hot-reload lifecycle business flow for skill_center — route-B physical e2e.

Exercises the full physical lifecycle of a skill from creation to filesystem-
visible hot-reload artifacts. Built for the route-B (live backend) executor;
route-A may skip FsAssert. Correct lifecycle path is:

  1. Create bot       → prepares the per-bot skill workspace
  2. Create skillset  → DB row, no FS change
  3. Create git skill → POST /api/skills writes ac_skill for public market material
  4. Upload local skill → POST /api/skills/upload writes skills-local material
  5. Add both to set → links DB + rewrites the bot workspace skill_sets.json

FsAssert paths are relative to LOCAL_AIDESKTOP_ROOT. Symlinks land in the
per-bot singlebox workspace, mirroring the online multi-arca isolation model.
link_name = skill name; target = skills-repo/<git path>.
"""
from tests.community.framework.flow import FlowCase, FlowFile, FlowStep, FsAssert

BOT_SKILLS_PATH = (
    "aidesktop_singlebox/bolt_data/staff_{user_id}/{bot_id}/openclaw/workspace/skills"
)

HOT_RELOAD_LIFECYCLE = FlowCase(
    name="skill_center-hot-reload-lifecycle",
    covers=["skill_center"],
    steps=[
        FlowStep(
            method="POST", path="/api/bots",
            body={"bot_name": "Acceptance Lifecycle Bot", "bot_type": "personal"},
            expect_status=200,
            extract={"user_id": "data.bot.owner_id", "bot_id": "data.bot.bot_id"},
            fs_asserts=[
                FsAssert(kind="is_dir", path=BOT_SKILLS_PATH),
                FsAssert(kind="is_symlink", path=f"{BOT_SKILLS_PATH}/skills-repo"),
            ],
        ),
        FlowStep(
            method="GET",
            path="/api/bots/{bot_id}/status",
            expect_status=200,
            expect={"success": True, "data": {"is_ready": True}},
            poll_timeout_sec=180,
            poll_interval_sec=2,
        ),
        FlowStep(
            method="POST", path="/api/skillsets",
            body={"name": "Lifecycle SkillSet", "user_id": "{user_id}", "bot_id": "{bot_id}"},
            expect_status=200,
            extract={"skill_set_id": "data.id"},
        ),
        # Bug1 fix prerequisite: POST /api/skills must accept git_path body field.
        FlowStep(
            method="POST", path="/api/skills",
            body={
                "name": "brainstorming",
                "git_path": "git://infra/common/brainstorming",
                "user_id": "{user_id}", "bot_id": "{bot_id}",
            },
            expect_status=200,
            extract={"git_skill_id": "data.id"},
        ),
        FlowStep(
            method="POST",
            path="/api/skills/upload",
            query={"user_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
            form={
                "file_paths": (
                    '["uploaded-hot-reload-skill/SKILL.md",'
                    '"uploaded-hot-reload-skill/README.md"]'
                )
            },
            files=[
                FlowFile(
                    field="files",
                    filename="uploaded-hot-reload-skill/SKILL.md",
                    content=(
                        "name: uploaded-hot-reload-skill\n"
                        "description: Uploaded local skill for hot-reload lifecycle\n"
                        "category: lifecycle\n"
                        "tags:\n"
                        "  - singlebox\n"
                        "  - local-upload\n"
                    ),
                    content_type="text/markdown",
                ),
                FlowFile(
                    field="files",
                    filename="uploaded-hot-reload-skill/README.md",
                    content="# Uploaded Hot Reload Skill\n",
                    content_type="text/markdown",
                ),
            ],
            expect_status=200,
            expect={"success": True, "data": {"name": "uploaded-hot-reload-skill"}},
            extract={"local_skill_id": "data.id"},
            fs_asserts=[
                FsAssert(
                    kind="is_file",
                    path=f"{BOT_SKILLS_PATH}/skills-local/uploaded-hot-reload-skill/SKILL.md",
                ),
            ],
        ),
        # Add both public-market git material and user-uploaded local material
        # to the same set. The metadata must preserve their distinct source
        # roots: skills-repo/... and skills-local/...
        FlowStep(
            method="POST", path="/api/skillsets/{skill_set_id}/skills",
            body={
                "skill_ids": ["{git_skill_id}", "{local_skill_id}"],
                "user_id": "{user_id}",
                "bot_id": "{bot_id}",
            },
            expect_status=200,
            expect={"success": True},
            # The current add-to-set path rewrites metadata. Actual symlink
            # materialization is covered by the activation/device-sync boundary.
            fs_asserts=[
                FsAssert(kind="is_file", path=f"{BOT_SKILLS_PATH}/skill_sets.json"),
            ],
        ),
    ],
)
