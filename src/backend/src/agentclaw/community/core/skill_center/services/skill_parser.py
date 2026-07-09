"""SkillParser - 统一的技能文件解析器

从 SKILL.md / README.md 文件中提取技能元数据。
纯工具类，不依赖数据库或缓存。
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from agentclaw.community.log import get_logger


logger = get_logger()


@dataclass
class SkillInfo:
    """技能信息数据模型 (文件系统层面)"""
    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    category: str = "general"
    icon: str = "🔧"
    path: str = ""  # 软链接路径 ~/.moltis/skills/{link_name}
    source_path: str = ""  # 源路径 ~/.openclaw/skills-repo/{relative_path}
    is_active: bool = False
    is_installed: bool = False
    capabilities: list[dict] = field(default_factory=list)
    author: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
            "icon": self.icon,
            "path": self.path,
            "source_path": self.source_path,
            "is_active": self.is_active,
            "is_installed": self.is_installed,
            "capabilities": self.capabilities,
            "author": self.author,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SkillTreeNode:
    """技能市场树节点"""
    name: str
    path: str
    type: str  # 'dir' or 'skill'
    children: list['SkillTreeNode'] = field(default_factory=list)
    skill_info: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "children": [c.to_dict() for c in self.children],
        }
        if self.skill_info:
            result["skill_info"] = self.skill_info
        return result


def _extract_frontmatter_fields(fm_data: dict, skill_info: dict) -> None:
    """从解析后的 YAML frontmatter dict 中提取字段到 skill_info。"""
    if "name" in fm_data:
        skill_info["name"] = fm_data["name"]
    if "description" in fm_data:
        skill_info["description"] = fm_data["description"]
    if "version" in fm_data:
        skill_info["version"] = fm_data["version"]
    if "author" in fm_data:
        skill_info["author"] = fm_data["author"]
    if "tags" in fm_data:
        tags = fm_data["tags"]
        if isinstance(tags, list):
            skill_info["tags"] = tags
        elif isinstance(tags, str):
            skill_info["tags"] = [t.strip() for t in tags.split(",") if t.strip()]


def _extract_description_from_body(body: str) -> str:
    """从 markdown body 中提取第一个标题后的描述文本。"""
    lines = body.split('\n')
    found_title = False
    description_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if found_title and description_lines:
                break
            continue
        if stripped.startswith('#'):
            if found_title:
                break
            found_title = True
            continue
        if found_title:
            description_lines.append(stripped)
            if len(description_lines) >= 2:
                break
    return ' '.join(description_lines)


class SkillParser:
    """统一解析 SKILL.md / README.md"""

    @staticmethod
    def find_skill_file(skill_path: Path) -> Path | None:
        """查找技能文件 (SKILL.md 优先，其次是 README.md)"""
        skill_file = skill_path / "SKILL.md"
        if skill_file.exists():
            return skill_file
        skill_file = skill_path / "README.md"
        if skill_file.exists():
            return skill_file
        return None

    @staticmethod
    def has_skill_file(skill_path: Path) -> bool:
        """检查目录是否包含 SKILL.md（严格的技能定义）"""
        return (skill_path / "SKILL.md").exists()

    @classmethod
    def parse(cls, skill_path: Path) -> dict[str, Any] | None:
        """解析技能文件，提取元数据"""
        skill_file = cls.find_skill_file(skill_path)
        if not skill_file:
            return None

        try:
            try:
                content = skill_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = skill_file.read_text(encoding="gbk", errors="replace")
            stat = skill_file.stat()
            created_at = datetime.fromtimestamp(stat.st_ctime).isoformat()
            updated_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
        except Exception as e:
            logger.error("[SkillParser] Error reading %s: %s", skill_file, e)
            return None

        skill_info = {
            "name": skill_path.name,
            "description": "",
            "version": "1.0.0",
            "category": "general",
            "author": "",
            "tags": [],
            "input_schema": "",
            "output_schema": "",
            "capabilities": [],
            "created_at": created_at,
            "updated_at": updated_at,
        }

        # 提取 YAML frontmatter
        body = content
        if content.strip().startswith("---"):
            frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            if frontmatter_match:
                frontmatter = frontmatter_match.group(1)
                body = frontmatter_match.group(2)

                # 使用 yaml 解析 frontmatter（支持多行字符串）
                try:
                    fm_data = yaml.safe_load(frontmatter)
                    if fm_data and isinstance(fm_data, dict):
                        _extract_frontmatter_fields(fm_data, skill_info)
                except Exception as e:
                    # yaml 解析失败时，回退到简单行解析（正常业务场景）
                    logger.debug("[SkillParser] YAML parse error, fallback to line parsing: %s", e)
                    for line in frontmatter.split("\n"):
                        if ":" in line:
                            key, value = line.split(":", 1)
                            key = key.strip()
                            value = value.strip()

                            if key == "name":
                                skill_info["name"] = value
                            elif key == "description":
                                skill_info["description"] = value
                            elif key == "version":
                                skill_info["version"] = value
                            elif key == "author":
                                skill_info["author"] = value
                            elif key == "tags":
                                tags_str = value.strip("[]")
                                skill_info["tags"] = [t.strip().strip('"\'') for t in tags_str.split(",") if t.strip()]

        # Note: 不再从 body 提取标题作为 name，因为代码块中的注释可能被误识别
        # 如果 frontmatter 没有 name，则使用目录名（已在初始化时设置）

        # 从 body 提取描述
        if not skill_info["description"]:
            skill_info["description"] = _extract_description_from_body(body)

        # 提取能力
        capability_sections = re.findall(r'^##\s+(.+)$', body, re.MULTILINE)
        for cap in capability_sections:
            skill_info["capabilities"].append({
                "id": cap.lower().replace(' ', '_'),
                "name": cap,
            })

        return skill_info

    @staticmethod
    def parse_content(content: str) -> dict[str, Any] | None:
        """解析 SKILL.md 内容字符串，提取元数据（不依赖文件系统）

        用于远程设备（如 Arca）场景，避免需要先写入文件再解析。
        """
        if not content:
            return None

        skill_info = {
            "name": "",
            "description": "",
            "version": "1.0.0",
            "category": "general",
            "author": "",
            "tags": [],
            "input_schema": "",
            "output_schema": "",
            "capabilities": [],
        }

        # 提取 YAML frontmatter
        body = content
        if content.strip().startswith("---"):
            frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            if frontmatter_match:
                frontmatter = frontmatter_match.group(1)
                body = frontmatter_match.group(2)

                # 使用 yaml 解析 frontmatter
                try:
                    fm_data = yaml.safe_load(frontmatter)
                    if fm_data and isinstance(fm_data, dict):
                        _extract_frontmatter_fields(fm_data, skill_info)
                except Exception as e:
                    logger.debug(f"[SkillParser.parse_content] YAML parse error: {e}")

        # 从 body 提取描述
        if not skill_info["description"]:
            skill_info["description"] = _extract_description_from_body(body)

        return skill_info
