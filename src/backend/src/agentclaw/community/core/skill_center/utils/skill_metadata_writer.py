"""
Skill set metadata writer for persisting skill set information to JSON.
"""
import json
from pathlib import Path

from agentclaw.community.core.repository.protocols.skill_center import SkillSetRepository
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.log import get_logger

logger = get_logger()

# ARCA container fallback. Business callers pass skills_dir explicitly via
# _get_bot_paths(path_factory, ...) — this default only fires on admin /
# migration paths that historically only ran inside the ARCA container.
SKILLS_DIR = Path("/home/admin/.openclaw/workspace/skills")


class SkillSetMetadataWriter:
    """Writes skill set metadata to a JSON file for external consumption."""

    def __init__(
        self,
        skill_set_repo: SkillSetRepository,
        skill_repo: SkillRepository,
        skills_dir: Path | None = None,
        user_id: str | None = None,
        bot_id: str | None = None,
    ):
        """
        Args:
            skill_set_repo: SkillSet repository (required).
            skill_repo: Skill repository (required).
            skills_dir: Directory for active skills. Production callers always
                pass an explicit ``skills_dir`` (computed by ``SkillSetService``
                from its bot context). Omit only when no bot context exists —
                falls back to the host default ``~/.moltis/skills``.
            user_id: User ID for filtering skill sets in the JSON output.
            bot_id: Bot ID (used as the ``bolt_id`` filter when reading skill sets).
        """
        self.user_id = user_id
        self.bot_id = bot_id
        self.skill_set_repo = skill_set_repo
        self.skill_repo = skill_repo

        # Production callers always pass ``skills_dir``; the fallback handles
        # bare ``SkillSetMetadataWriter()`` constructions in legacy/test code
        # that operate against the host-default skills root.
        self.skills_dir = skills_dir or SKILLS_DIR
        self.METADATA_FILE = self.skills_dir / "skill_sets.json"

        logger.info(f"[SkillSetMetadataWriter] Initialized: skills_dir={self.skills_dir}, metadata_file={self.METADATA_FILE}, user_id={user_id}")

    def _get_active_skill_set_ids(self) -> set:
        """Get currently active skill set IDs from database (is_active=1).

        Falls back to .current_skill_set file if DB has no active sets.
        """
        # 优先从 DB 读取 is_active（按 user_id + bolt_id 过滤）
        try:
            all_sets = self.skill_set_repo.list_all(user_id=self.user_id, bolt_id=self.bot_id)
            active_ids = {s['id'] for s in all_sets if s.get('is_active')}
            if active_ids:
                logger.debug(f"[SkillSetMetadataWriter] Active skill set IDs from DB: {active_ids}")
                return active_ids
        except Exception as e:
            logger.warning(f"[SkillSetMetadataWriter] Failed to read active sets from DB: {e}")

        # Fallback: 从文件读取（兼容旧的 SkillSetSwitcher 模式）
        current_set_file = self.skills_dir / ".current_skill_set"
        try:
            if current_set_file.exists():
                data = json.loads(current_set_file.read_text())
                skill_set_id = data.get("skill_set_id")
                if skill_set_id:
                    logger.debug(f"[SkillSetMetadataWriter] Current skill set ID from file: {skill_set_id}")
                    return {skill_set_id}
        except Exception as e:
            logger.warning(f"[SkillSetMetadataWriter] Failed to read current skill set file: {e}")
        return set()

    def _get_skill_dir_name(self, git_path: str | None) -> str:
        """Extract skill directory name from git_path.

        Args:
            git_path: Path in format "git://category/subcategory/skill" or "local://skill-name"

        Returns:
            Directory name of the skill (e.g., "skill" from "git://cat/sub/skill")
        """
        if not git_path:
            return ""

        try:
            if git_path.startswith("git://"):
                # git://category/subcategory/skill-name -> skill-name
                rel_path = git_path[6:]  # Remove "git://"
                return Path(rel_path).name
            elif git_path.startswith("local://"):
                # local://skill-name -> skill-name
                return Path(git_path[8:]).name  # Remove "local://"
            else:
                # Fallback: treat as path and get basename
                return Path(git_path).name
        except Exception:
            return ""

    def _get_skill_absolute_path(self, git_path: str | None) -> str:
        """Convert git_path to absolute path.

        Args:
            git_path: Path in format "git://category/subcategory/skill" or "local://skill-name"

        Returns:
            Absolute path to the skill directory (resolved symlink if applicable)
        """
        if not git_path:
            return ""

        try:
            # Derive repo and local dirs from skills_dir
            # skills_dir: .../skills -> repo_dir: .../skills-repo, local_dir: .../skills-local
            repo_dir = self.skills_dir.parent / "skills-repo"
            local_dir = self.skills_dir.parent / "skills-local"

            # If repo_dir is a symlink, resolve it to get the real path
            # e.g., /aidesktop/.../skills-repo -> /home/admin/.bolt_shared/skills-repo
            if repo_dir.is_symlink():
                try:
                    repo_dir = repo_dir.resolve()
                except Exception:
                    pass  # Keep original if resolve fails

            if git_path.startswith("git://"):
                # Git skill: remove prefix and join with repo dir
                rel_path = git_path[6:]  # Remove "git://"
                return str(repo_dir / rel_path)
            elif git_path.startswith("local://"):
                # Local skill: remove prefix and join with local dir
                skill_name = git_path[8:]  # Remove "local://"
                return str(local_dir / skill_name)
            else:
                # Fallback: treat as relative path in repo
                return str(repo_dir / git_path)
        except Exception:
            # If any error, return empty string
            return ""

    def write_metadata(self, user_id: str | None = None) -> None:
        """
        Write all skill sets metadata to JSON file.

        This method reads all skill sets from the database and writes
        them to a JSON file in ~/.moltis/skills/skill_sets.json.
        Uses atomic write (write to tmp then rename) for consistency.

        Args:
            user_id: User ID for filtering skill sets (defaults to self.user_id)
        """
        # Use provided user_id or instance user_id
        effective_user_id = user_id or self.user_id

        logger.info(f"[SkillSetMetadataWriter.write_metadata] Writing metadata to {self.METADATA_FILE}, user_id={effective_user_id}")
        try:
            data = {"skill_sets": []}
            active_ids = self._get_active_skill_set_ids()

            # Query all skill sets using repository (filtered by user_id + bolt_id)
            skill_sets = self.skill_set_repo.list_all(user_id=effective_user_id, bolt_id=self.bot_id)
            logger.info(f"[SkillSetMetadataWriter.write_metadata] Found {len(skill_sets)} skill sets for user_id={effective_user_id}, bolt_id={self.bot_id}, active_ids={active_ids}")

            for skill_set in skill_sets:
                # Get skills for this skill set via repository
                skills = self.skill_set_repo.get_skills_in_set(skill_set['id'])

                skill_set_data = {
                    "name": skill_set['name'],
                    "description": skill_set.get('description') or "",
                    "is_current": skill_set['id'] in active_ids,
                    "skills": [
                        {
                            "name": skill['name'],
                            "description": skill.get('description') or "",
                            "skill": self._get_skill_dir_name(skill.get('git_path')),
                            "path": self._get_skill_absolute_path(skill.get('git_path'))
                        }
                        for skill in skills
                    ]
                }
                data["skill_sets"].append(skill_set_data)

            # Ensure directory exists
            self.METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: write to tmp file then rename
            tmp_file = self.METADATA_FILE.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp_file.rename(self.METADATA_FILE)

            logger.info(f"[SkillSetMetadataWriter.write_metadata] Success: metadata written to {self.METADATA_FILE} with {len(data['skill_sets'])} skill sets")

        except Exception as e:
            # Write failure should not block main flow
            import traceback
            logger.error(f"[SkillSetMetadataWriter.write_metadata] Failed to write metadata: {e}")
            logger.error(traceback.format_exc())


def get_metadata_writer(
    skill_set_repo: SkillSetRepository,
    skill_repo: SkillRepository,
    skills_dir: Path | None = None,
    user_id: str | None = None,
    bot_id: str | None = None,
):
    """Get SkillSetMetadataWriter instance."""
    return SkillSetMetadataWriter(
        skill_set_repo=skill_set_repo,
        skill_repo=skill_repo,
        skills_dir=skills_dir,
        user_id=user_id,
        bot_id=bot_id,
    )
