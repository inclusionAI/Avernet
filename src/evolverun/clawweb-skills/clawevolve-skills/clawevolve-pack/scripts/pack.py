#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack.py — agent 镜像打包核心（clawevolve-pack）。

把当前 bot 的 agent 配置物料（md / mcp / skill）打成不可变自包含 .zip 镜像，
用于基线回归。范围与规则见 ../SKILL.md。

设计要点：
  - handler 注册表驱动（PACK_HANDLERS）；新增打包项 = 加 handler + 加 --with-<layer>
    开关 + 一条 manifest layer，不改既有逻辑（patch 洞）。
  - skill 层是 workspace/skills 与 workspace/skills-local 的目录快照；普通文件、目录和软链原样保存。
    软链只保存 readlink target，不跟随、不解析、不要求 target 存在。
  - MCP 配置按原始字节保存，不解析、不格式化、不脱敏。
  - manifest 为开放数组 layers；skill layer 使用 Pack/Deploy 同代 schema v3。
仅依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 3
MIN_OPENCLAW_VERSION = "2026.5.22"
MIN_CLAWEVOLVE_DEPLOY_VERSION = "clawevolve-deploy-20260821-v4"
DEFAULT_EVOLVE_RESULTS_BASE = "/home/admin/.openclaw/workspace/clawevolve_results"
DEFAULT_MAX_ARTIFACT_MB = float(os.environ.get("CLAWEVOLVE_MAX_ARTIFACT_MB", "100"))

# 顶层 md:persona 配置白名单(与 SKILL.md L24 对齐:保留 persona 配置,排除运行时
# 文档如 fix_record / SPEC / clawbench_review)。MEMORY/HEARTBEAT 是运行时状态也排除。
# 白名单制让镜像只含 1:1 复现需要的 persona,避免把工作文档当作配置带走。
MD_INCLUDE = {
    "SOUL.md", "AGENTS.md", "TOOLS.md", "IDENTITY.md",
    "RULES.md", "OKR.md", "USER.md", "BOOTSTRAP.md",
}
MD_EXCLUDE = {"MEMORY.md", "HEARTBEAT.md"}  # 保留作 manifest excluded 记录展示
# clawevolve_results 等非 Skill layer 沿用既有运行噪声过滤；Skill snapshot
# 单独采用“尽量完整保存”策略，只跳过无法稳定操作的 .nfs*。
PACK_NOISE_NAMES = {".DS_Store", "__MACOSX", "__pycache__", ".git"}
RELEASE_MANAGED_SKILL_PREFIXES = ("clawevolve-", "clawbench-", "ocb-")


def _is_release_managed_skill_name(name: str) -> bool:
    """ClawEvolve release assets are runtime dependencies, not Bot state."""
    value = str(name or "")
    candidate = value[1:] if value.startswith(".") else value
    candidate = candidate.split(".backup.", 1)[0]
    return candidate.startswith(RELEASE_MANAGED_SKILL_PREFIXES)


def _is_pack_noise_name(name: str) -> bool:
    """Exclude metadata/runtime handles that are never deployable skill state."""
    return name in PACK_NOISE_NAMES or name.endswith(".pyc") or name.startswith(".nfs")


def _ignore_pack_noise(_directory, names):
    return {name for name in names if _is_pack_noise_name(name)}


def _is_skill_snapshot_transient_name(name: str) -> bool:
    """Only NFS orphan handles are excluded from the Skill filesystem snapshot."""
    return str(name or "").startswith(".nfs")


def _is_python_runtime_cache_name(name: str) -> bool:
    """Python bytecode caches are runtime state, never Bot Skill source."""
    value = str(name or "")
    return value == "__pycache__" or value.endswith(".pyc")
# 预留扩展开关:当前命中即报"未支持/待 patch"
RESERVED_WITH_FLAGS = {
    "with-model": "模型 id+参数 (apiKey→auth_ref) -- 待 patch,优先级最高",
    "with-memory": "workspace/memory/ 与 MEMORY.md -- 待 patch",
    "with-plugins": "插件/扩展 + 运行时旋钮 (compaction/tools.profile/skills.entries/plugins.entries) -- 待 patch",
    "with-activeset": "活跃技能集元数据 (skill_sets.json/.current_skill_set/skill_parameters.json) -- 待 patch",
    "with-identity": "device identity (跨机需重新配对) -- 待 patch",
    "with-sessions": "会话历史 .jsonl -- 待 patch",
}

# ─── 工具函数 ────────────────────────────────────────────────────────────────

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> tuple[str, int]:
    """目录的确定性摘要:排序 (relpath, file_sha256) 拼接后 sha256。返回 (digest, file_count)。"""
    h = hashlib.sha256()
    count = 0
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(sha256_file(p).encode())
        h.update(b"\0")
        count += 1
    return h.hexdigest(), count


def rel_sha256_and_items(path: Path) -> tuple[str, int]:
    if path.is_dir():
        return sha256_tree(path)
    if path.is_file():
        return sha256_file(path), 1
    return "", 0


def _skill_layer_sha256(staging: Path) -> tuple[str, int]:
    """Deterministic digest of both skill snapshot roots.

    The digest includes root presence, directory entries (including empty dirs),
    regular-file content and symlink targets.  Symlink targets are opaque data and
    are never resolved.
    """
    digest = hashlib.sha256()
    count = 0
    for root_name in ("skills", "skills-local"):
        root = staging / root_name
        present = root.is_dir() and not root.is_symlink()
        digest.update(f"R\0{root_name}\0{int(present)}\0".encode())
        if not present:
            continue
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            for name in sorted(list(dirnames)):
                path = base / name
                rel = path.relative_to(staging).as_posix()
                if path.is_symlink():
                    digest.update(f"L\0{rel}\0{os.readlink(path)}\0".encode())
                    count += 1
                    dirnames.remove(name)
                else:
                    digest.update(f"D\0{rel}\0".encode())
                    count += 1
            for name in sorted(filenames):
                path = base / name
                rel = path.relative_to(staging).as_posix()
                if path.is_symlink():
                    digest.update(f"L\0{rel}\0{os.readlink(path)}\0".encode())
                else:
                    digest.update(f"F\0{rel}\0{sha256_file(path)}\0".encode())
                count += 1
    return digest.hexdigest(), count


def detect_workspace(arg: str | None) -> Path | None:
    if arg:
        return Path(arg).expanduser()
    for cand in (
        os.environ.get("WORKSPACE"),
        (os.path.join(os.environ["OPENCLAW_HOME"], "workspace")
         if os.environ.get("OPENCLAW_HOME") else None),
        os.path.expanduser("~/.openclaw/workspace"),
    ):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return None


def read_first_lines(p: Path, n: int = 6) -> str:
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readline() for _ in range(n)).strip()
    except Exception:
        return ""

def read_first_lines_skip_frontmatter(p: Path, n: int = 6) -> str:
    """读取文件前 n 行，跳过 YAML frontmatter 分隔线 --- """
    text = read_first_lines(p, n)
    # 跳过 YAML frontmatter 分隔线
    text = re.sub(r'^---\s*$', '', text, flags=re.M).strip()
    return text


def derive_meta(ws: Path, bot_id: str | None) -> tuple[str, str, str]:
    # 优先级：--bot-id > $BOT_ID > $USER@hostname > 默认。沙箱常无 $USER 且 hostname 多为
    # 不可读随机串（如 bot-sandbox-2dibwn3bqn7btveihlvgtlmwsy）→ 用可读默认，避免镜像名
    # 带 30 字符随机串。hostname 短且像人名时保留。
    if bot_id:
        id_ = bot_id.strip()
    elif os.environ.get("BOT_ID"):
        id_ = os.environ["BOT_ID"].strip()
    else:
        user = (os.environ.get("USER") or "").strip()
        host = os.uname().nodename
        if user:
            id_ = f"{user}@{host}"
        else:
            # 沙箱常无 $USER；hostname 若含 20+ 字符无分隔的随机串
            # （如 bot-sandbox-2dibwn3bqn7btveihlvgtlmwsy 里的 2dibwn3bqn7btveihlvgtlmwsy）
            # 视为不可读 → 用可读默认；带 "-"/"." 分隔的可读 hostname 保留。
            id_ = "openclaw-bot" if re.search(r"[A-Za-z0-9]{20,}", host) else host
    id_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", id_).strip("-") or "bot"

    title = ""
    for name in ("IDENTITY.md", "SOUL.md", "AGENTS.md"):
        f = ws / name
        if f.exists():
            head = read_first_lines_skip_frontmatter(f, 6)
            m = re.search(r"^(?:#\s*)?(.+)$", head)
            if m:
                title = m.group(1).strip().lstrip("#").strip()
                break
    title = title or id_slug
    desc = read_first_lines(ws / "SOUL.md", 8) if (ws / "SOUL.md").exists() else ""
    desc = re.sub(r"\s+", " ", desc)[:160]
    return id_slug, title, desc


# ─── 物料 handler(注册表)──────────────────────────────────────────────────

def handler_md(ws: Path, staging: Path, args) -> dict:
    dst = staging / "md"
    dst.mkdir(parents=True, exist_ok=True)
    files = []
    skipped_non_persona = []
    for p in sorted(ws.glob("*.md")):
        if p.name in MD_INCLUDE:
            shutil.copy2(p, dst / p.name)
            files.append(p.name)
        else:
            # 非 persona(MEMORY/HEARTBEAT 运行时状态 + fix_record/SPEC 等工作文档)排除
            skipped_non_persona.append(p.name)
    digest, n = rel_sha256_and_items(dst) if files else ("", 0)
    return {"name": "md", "path": "md/", "sha256": digest, "files": n,
            "names": files, "excluded": sorted(set(skipped_non_persona) | MD_EXCLUDE)}


def handler_mcp(ws: Path, staging: Path, args) -> dict:
    # 候选源可能本身是软链(A 的常见布局:workspace/config/mcporter.json → ~/.mcporter/mcporter.json,
    # target 在 workspace 之外)。显式 realpath 解到底再判定存在性,避免 is_file() 对断链软链
    # 静默返回 False、被当成"未配置 MCP"误 skip。与 handler_skill 同策略。
    candidates = [ws / "config" / "mcporter.json", ws / ".mcporter.json"]
    src = None
    skip_reason = "未找到 mcporter.json(该 bot 未配置 MCP)"
    for c in candidates:
        if not c.exists() and not c.is_symlink():
            continue
        real = Path(os.path.realpath(c))
        if real.is_file():
            src = real
            break
        skip_reason = f"mcporter.json 软链断链:{c} → {real}(target 不存在)"
    if src is None:
        return {"name": "mcp", "path": "mcp/mcporter.json", "sha256": "",
                "skipped": True, "reason": skip_reason}
    dst = staging / "mcp"
    dst.mkdir(parents=True, exist_ok=True)
    try:
        (dst / "mcporter.json").write_bytes(src.read_bytes())
    except OSError as e:
        return {"name": "mcp", "path": "mcp/mcporter.json", "sha256": "",
                "skipped": True, "reason": f"mcporter.json 读取失败: {e}", "source": str(src)}
    digest, n = rel_sha256_and_items(dst)
    return {"name": "mcp", "path": "mcp/mcporter.json", "sha256": digest,
            "source": str(src), "contentMode": "raw-bytes"}


def _is_skill_snapshot_excluded(name: str) -> bool:
    return _is_release_managed_skill_name(name)


READONLY_SKILL_ENTRY_NAMES = {"skills-repo", "skills-center"}


def _is_personal_skill_link_target(target: str) -> bool:
    """Whether an activation link syntactically targets a personal Skill root."""
    value = str(target or "").replace("\\", "/").rstrip("/")
    if not value:
        return False
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if len(parts) >= 2 and parts[0] == "skills-local":
        return True
    if len(parts) >= 3 and parts[0] == ".." and parts[1] == "skills-local":
        return True
    return "/workspace/skills/skills-local/" in value or "/workspace/skills-local/" in value


def _snapshot_copy_tree(source: Path, destination: Path, excluded: list[str], rel_root: str = "",
                        personal_skills_top_only: bool = False) -> dict:
    """Copy a snapshot root without following symlinks.

    Release-managed names are filtered only at the documented root-entry levels:
    ``skills/<name>``, ``skills/skills-local/<name>`` and
    ``skills-local/<name>``. A user skill's internal files are never filtered
    merely because they happen to share a release prefix.
    """
    stats = {"files": 0, "directories": 0, "symlinks": 0, "items": 0, "excluded": excluded}
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"Skill snapshot root 不是普通目录: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    root_name = Path(rel_root).parts[0] if rel_root else source.name

    def should_exclude(rel: Path, name: str) -> bool:
        if not _is_skill_snapshot_excluded(name):
            return False
        parts = rel.parts
        return ((root_name in {"skills", "skills-local"} and len(parts) == 1) or
                (root_name == "skills" and len(parts) == 2 and parts[0] == "skills-local"))

    def visit(src: Path, dst: Path, rel: Path) -> None:
        try:
            children = sorted(src.iterdir(), key=lambda x: x.name)
        except OSError as e:
            raise RuntimeError(f"读取 Skill snapshot 失败: {src}: {e}") from e
        for child in children:
            name = child.name
            child_rel = rel / name
            if _is_python_runtime_cache_name(name):
                excluded.append(f"{child_rel.as_posix()}(python runtime cache)")
                continue
            if _is_skill_snapshot_transient_name(name):
                excluded.append(f"{child_rel.as_posix()}(nfs transient)")
                continue
            if should_exclude(child_rel, name):
                excluded.append(f"{child_rel.as_posix()}(Release managed)")
                continue
            if personal_skills_top_only and not rel.parts and name != "skills-local":
                if name in READONLY_SKILL_ENTRY_NAMES:
                    excluded.append(f"{child_rel.as_posix()}(read-only shared Skill)")
                    continue
                if child.is_symlink() and not _is_personal_skill_link_target(os.readlink(child)):
                    excluded.append(f"{child_rel.as_posix()}(public Skill activation)")
                    continue
            target = dst / name
            try:
                mode = child.lstat().st_mode
                if child.is_symlink():
                    os.symlink(os.readlink(child), target)
                    stats["symlinks"] += 1
                    stats["items"] += 1
                elif child.is_dir():
                    target.mkdir()
                    stats["directories"] += 1
                    stats["items"] += 1
                    visit(child, target, child_rel)
                elif child.is_file():
                    shutil.copy2(child, target, follow_symlinks=False)
                    stats["files"] += 1
                    stats["items"] += 1
                else:
                    raise RuntimeError(f"不支持的 Skill snapshot 文件类型: {child}")
                if not child.is_symlink():
                    os.chmod(target, mode & 0o7777)
            except (OSError, shutil.Error) as e:
                raise RuntimeError(f"复制 Skill snapshot 失败: {child}: {e}") from e
    visit(source, destination, Path())
    return stats


def handler_skill(ws: Path, staging: Path, args) -> dict:
    """Snapshot personal Skill entities and activation entries only."""
    roots = []
    excluded: list[str] = []
    total = {"files": 0, "directories": 0, "symlinks": 0, "items": 0}
    for root_name in ("skills", "skills-local"):
        source = ws / root_name
        destination = staging / root_name
        present = source.exists() or source.is_symlink()
        if present:
            if source.is_symlink() or not source.is_dir():
                raise RuntimeError(f"Skill snapshot root 必须是普通目录: {source}")
            destination.mkdir(parents=True, exist_ok=True)
            stats = _snapshot_copy_tree(
                source, destination, excluded, root_name,
                personal_skills_top_only=(root_name == "skills"),
            )
            for key in total:
                total[key] += stats[key]
        roots.append({"path": f"{root_name}/", "present": bool(present)})

    digest, digest_items = _skill_layer_sha256(staging)
    return {
        "name": "skill",
        "sha256": digest,
        "items": digest_items,
        "files": total["files"],
        "directories": total["directories"],
        "symlinks": total["symlinks"],
        "skills": [],
        "excluded": excluded,
        "snapshot": {
            "roots": roots,
            "symlinkPolicy": "preserve",
            "dereferenceSymlinks": False,
            "digestAlgorithm": "personal-skill-snapshot-v1",
            "excludedSkillPrefixes": ["clawevolve-", "clawbench-", "ocb-"],
            "scope": "personal-skills",
        },
    }


def resolve_evolve_results_dir(args) -> Path | None:
    """Resolve the evolve run directory without scanning global state.

    Never package the global evolve_results root by default, because the same bot can
    have multiple concurrent evolve runs.
    """
    if getattr(args, "evolve_results_dir", None):
        return Path(args.evolve_results_dir).expanduser()
    if getattr(args, "evolve_run_id", None):
        rid = str(args.evolve_run_id).strip().strip("/")
        if rid:
            return Path(DEFAULT_EVOLVE_RESULTS_BASE) / rid
    env_dir = os.environ.get("EVOLVE_RUN_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return None

def handler_evolve_results(ws: Path, staging: Path, args) -> dict:
    """Copy the self-evolution run artifacts into the image.

    Source priority:
      --evolve-results-dir
      --evolve-run-id -> /home/admin/.openclaw/workspace/clawevolve_results/<run-id>
      $EVOLVE_RUN_DIR
      skipped if none is available

    This directory contains run-level objective.md/run_manifest.json and round-level
    inputs/results such as rounds/round-000/input/spec-v0.md, normalized bench
    results, tune reports, diffs, and rounds/round-000/spec/spec-v1.md. It is intentionally independent
    from `ws` because ClawBench/evolve artifacts live under the OpenClaw workspace
    root, not under persona md/mcp/skills layers.
    """
    src = resolve_evolve_results_dir(args)
    if src is None:
        return {"name": "clawevolve_results", "path": "clawevolve_results/", "sha256": "",
                "skipped": True, "reason": "no evolve run bound; pass --evolve-results-dir/--evolve-run-id or set EVOLVE_RUN_DIR"}
    if not src.exists():
        return {"name": "clawevolve_results", "path": "clawevolve_results/", "sha256": "",
                "skipped": True, "reason": f"evolve results dir not found: {src}", "source": str(src)}
    if not src.is_dir():
        return {"name": "clawevolve_results", "path": "clawevolve_results/", "sha256": "",
                "skipped": True, "reason": f"evolve results path is not a directory: {src}", "source": str(src)}

    dst = staging / "clawevolve_results"

    try:
        shutil.copytree(src, dst, symlinks=True, ignore=_ignore_pack_noise, dirs_exist_ok=False)
        digest, n = rel_sha256_and_items(dst)
    except (FileNotFoundError, OSError, shutil.Error) as e:
        # Keep pack robust for large/remote evolve_results directories: an OSS/FUSE
        # race or transient I/O failure should not crash the whole image build.
        # Match handler_skill's skills-local behavior: remove partial copy and
        # record the layer as skipped with an explicit reason.
        shutil.rmtree(dst, ignore_errors=True)
        return {
            "name": "clawevolve_results",
            "path": "clawevolve_results/",
            "sha256": "",
            "skipped": True,
            "reason": f"evolve_results copy failed: {type(e).__name__}: {str(e)[:120]}",
            "source": str(src),
        }

    return {"name": "clawevolve_results", "path": "clawevolve_results/", "sha256": digest,
            "items": n, "source": str(src)}


PACK_HANDLERS = {"md": handler_md, "mcp": handler_mcp, "skill": handler_skill, "clawevolve_results": handler_evolve_results}


# ─── 最小 YAML 输出器(无外部依赖;支持嵌套 dict/list 块序列)───────────────

def to_yaml(obj, indent=0) -> str:
    pad = "  " * indent
    out: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and v:
                out.append(f"{pad}{k}:")
                out.append(to_yaml(v, indent + 1).rstrip())
            elif isinstance(v, list) and v:
                out.append(f"{pad}{k}:")
                out.append(to_yaml(v, indent + 1).rstrip())
            elif isinstance(v, (dict, list)):  # 空 dict/list
                out.append(f"{pad}{k}: {'{}' if isinstance(v, dict) else '[]'}")
            else:
                out.append(f"{pad}{k}: {_yaml_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                inner = to_yaml(item, indent + 1)
                first, *rest = inner.splitlines()
                out.append(f"{pad}- {first.strip()}")
                out.extend(rest)
            elif isinstance(item, list):
                out.append(f"{pad}-")
                out.append(to_yaml(item, indent + 1).rstrip())
            else:
                out.append(f"{pad}- {_yaml_scalar(item)}")
    else:
        out.append(f"{pad}{_yaml_scalar(obj)}")
    return "\n".join(out) + "\n" if out else ""


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if (s == "" or s.lower() in ("null", "true", "false", "yes", "no", "none")
            or re.search(r"[:\#\{\}\[\],&*?|>!%@`]", s) or s.startswith(("-", " ", "?"))):
        return json.dumps(s, ensure_ascii=False)
    return s


# ─── README ────────────────────────────────────────────────────────────────

def write_readme(staging: Path, fname: str, manifest_digest: str):
    (staging / "README.md").write_text(
        f"# {fname}\n\n"
        "agent 镜像(基线回归用)。内容包含 persona md / MCP 原文 / 两个 Skill 目录快照。evolve_results 默认不打包，需 --include-evolve-results 显式 opt-in。\n\n"
        "## 部署\n"
        "由 clawevolve-deploy(后续步骤)三段式铺设到 eval-carrier bot 沙箱:\n"
        "  md → workspace/*.md;mcp → workspace/config/mcporter.json(凭证由 ocb 按 owner 经 user_mcp_config 还原);\n"
        "  skill → 原样恢复 workspace/skills/ 与 workspace/skills-local/ 两个目录快照；clawevolve_results 仅在 opt-in 时恢复到 /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>。\n\n"
        "## 安全\n"
        f"内容摘要 `{manifest_digest[:16]}...`。Pack 按快照策略保留配置原文；含 persona/能力面和可能的凭证信息，必须按敏感文件对待并设置访问控制。\n",
        encoding="utf-8")


# ─── 上传(可选,clawweb 收镜像接口)────────────────────────────────────────

def upload(tar_path: Path, url: str) -> tuple[bool, str]:
    if shutil.which("curl"):
        try:
            r = subprocess.run(
                ["curl", "-sS", "-o", "-", "-w", "\\n%{http_code}", "-X", "POST", url,
                 "-F", f"file=@{tar_path}"],
                capture_output=True, text=True, timeout=120)
            code = r.stdout.strip().splitlines()[-1] if r.stdout else "?"
            return code.startswith("2"), f"curl http={code} {r.stderr.strip()[:200]}"
        except Exception as e:
            return False, f"curl 异常: {e}"
    import urllib.request
    try:
        with tar_path.open("rb") as f:
            req = urllib.request.Request(url, data=f, method="POST",
                headers={"Content-Type": "application/octet-stream"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return 200 <= resp.status < 300, f"urllib status={resp.status}"
    except Exception as e:
        return False, f"urllib 异常: {e}"


# ─── 主流程 ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pack.sh", description="agent 镜像打包(md/mcp/skill,基线回归用)")
    ap.add_argument("--version", default="1.0.0", help="镜像版本(semver,默认 1.0.0)")
    ap.add_argument("--workspace", default=None, help="源 workspace(默认探测 ~/.openclaw/workspace)")
    ap.add_argument("--evolve-results-dir", default=None,
                    help="自进化单次 run 目录；优先级最高，例如 /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>")
    ap.add_argument("--evolve-run-id", default=None,
                    help=f"自进化 run id；会解析为 {DEFAULT_EVOLVE_RESULTS_BASE}/<evolve-run-id>")
    ap.add_argument("--bot-id", default=None, help="bot 标识(写入 manifest source.carrierBotId)")
    ap.add_argument("--out-dir", default=".", help="tar 输出目录(默认当前目录)")
    ap.add_argument("--upload-url", default=None, help="可选:打完即 curl 上传 clawweb 收镜像接口")
    ap.add_argument("--ignore-secret-pattern", action="append", default=[], metavar="REGEX",
                    help="兼容旧命令保留；当前 Pack 不执行明文扫描")
    ap.add_argument("--allow-broken-symlink", action="store_true",
                    help="兼容旧命令保留；不再放行私有 Skill 越界或断链")
    ap.add_argument("--include-evolve-results", action="store_true",
                    help="显式 opt-in 打包 clawevolve_results；默认排除，避免历史轮次产物递归膨胀")
    ap.add_argument("--max-artifact-mb", type=float, default=DEFAULT_MAX_ARTIFACT_MB,
                    help="artifact 大小上限 MiB；默认 100，设 0 禁用")
    # 预留扩展开关:命中即报"未支持/待 patch"
    for flag in RESERVED_WITH_FLAGS:
        ap.add_argument(f"--{flag}", action="store_true", help=RESERVED_WITH_FLAGS[flag])
    args = ap.parse_args(argv)

    for flag in RESERVED_WITH_FLAGS:
        if getattr(args, flag.replace("-", "_")):
            sys.exit(f"clawevolve-pack: --{flag} 当前未支持(待 patch)。{RESERVED_WITH_FLAGS[flag]}")

    ws = detect_workspace(args.workspace)
    if not ws or not ws.is_dir():
        sys.exit("clawevolve-pack: 找不到 workspace(用 --workspace 指定,或设 $WORKSPACE/$OPENCLAW_HOME)。")
    ws = ws.resolve()

    id_slug, title, desc = derive_meta(ws, args.bot_id)

    with tempfile.TemporaryDirectory(prefix="clawevolve-pack-") as tmp:
        staging = Path(tmp) / "package"
        staging.mkdir(parents=True)

        layers = []
        layer_names = ["md", "mcp", "skill"]
        if args.include_evolve_results:
            layer_names.append("clawevolve_results")
        else:
            layers.append({
                "name": "clawevolve_results",
                "path": "clawevolve_results/",
                "sha256": "",
                "skipped": True,
                "reason": "excluded by default; pass --include-evolve-results to opt in",
            })
        for name in layer_names:
            try:
                rec = PACK_HANDLERS[name](ws, staging, args)
            except Exception as e:
                sys.exit(f"clawevolve-pack: 物料 {name} 打包失败: {e}")
            layers.append(rec)
            if rec.get("skipped") is True:
                status = "skipped"
            else:
                item_count = rec.get('files', rec.get('items', 0))
                if name == "mcp":
                    status = "ok (mcporter.json)" if item_count else "ok (mcporter.json)"
                else:
                    status = f"ok ({item_count} items)"
            print(f"  [layer] {name}: {status}", file=sys.stderr)

        # 内容摘要(用于文件名 sha8 + 清单 source.assertion)
        content_h = hashlib.sha256()
        for rec in layers:
            content_h.update(rec["name"].encode())
            content_h.update(rec.get("sha256", "").encode())
        content_digest = content_h.hexdigest()
        sha8 = content_digest[:8]

        # content_digest 基于 layers 的 name+sha256 拼接得出（不含 manifest 自身），
        # 故 manifest 加 source.contentDigest 字段不影响 content_digest 计算。
        # 文件名内嵌 sha8 = content_digest[:8]，与 deploy 侧 manifest.contentDigest
        # 校验同源（防传输损坏/版本错位，避免 self-referential 自指）。
        fname = f"{id_slug}__v{args.version}__{sha8}.zip"
        manifest = {
            "id": id_slug,
            "version": args.version,
            "title": title,
            "description": desc,
            "compat": {
                "schemaVersion": SCHEMA_VERSION,
                "minOpenclawVersion": MIN_OPENCLAW_VERSION,
                "minClawevolveDeployVersion": MIN_CLAWEVOLVE_DEPLOY_VERSION,
            },
            "source": {
                "openclawVersion": _guess_openclaw_version(),
                "host": os.uname().nodename,
                "user": os.environ.get("USER", ""),
                "carrierBotId": args.bot_id or "",
                "capturedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "contentDigest": content_digest,
                "contentSha8": sha8,
            },
            "secrets": {"plaintext": "preserved", "scheme": "raw", "resolver": "none"},
            "layers": layers,
            "excludes": ["skill:clawevolve-*", "skill:clawbench-*", "skill:ocb-*", "skill:.nfs*",
                         "md:MEMORY.md", "md:HEARTBEAT.md",
                         "clawevolve_results:.DS_Store", "clawevolve_results:__pycache__",
                         "clawevolve_results:*.pyc", "memory"],
            "flags": {"withMemory": False, "withModel": False, "withPlugins": False,
                      "withActiveset": False, "withIdentity": False, "withSessions": False,
                      "includeEvolveResults": bool(args.include_evolve_results),
                      "maxArtifactMb": float(args.max_artifact_mb or 0)},
        }
        (staging / "agent.image.yaml").write_text(to_yaml(manifest), encoding="utf-8")
        write_readme(staging, fname, content_digest)

        out_dir = Path(args.out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        zip_path = out_dir / fname
        # 用 zipfile 写 .zip。zipfile 不原生支持 Unix symlink，手动构造 ZipInfo：
        # external_attr 高 16 位存 Unix st_mode（symlink = 0o120000），body 存 target 字符串。
        # deploy 侧解包时按 external_attr 检测 symlink 并重建（见 deploy.py _extract_zip）。
        # 这样与 .tgz 行为对等：顶层 skill 软链 target 字符串原样保留，断链/通链都能复现。
        _write_zip_with_symlinks(staging, zip_path)
        zip_sha = sha256_file(zip_path)
        size = zip_path.stat().st_size
        max_mb = float(args.max_artifact_mb or 0)
        if max_mb > 0 and size > max_mb * 1024 * 1024:
            try:
                zip_path.unlink()
            except Exception:
                pass
            sys.exit(f"clawevolve-pack: artifact too large: {size} bytes > {max_mb} MiB; refusing to produce polluted image")

        print(f"\nclawevolve-pack: 已生成镜像", file=sys.stderr)
        print(f"  path:        {zip_path}", file=sys.stderr)
        print(f"  size:        {size} bytes ({size/1024:.1f} KiB)", file=sys.stderr)
        print(f"  sha256:      {zip_sha}", file=sys.stderr)
        print(f"  content:     {content_digest}", file=sys.stderr)
        for rec in layers:
            tag = "SKIP" if rec.get("skipped") is True else rec.get("sha256", "")[:16]
            print(f"  layer {rec['name']:<6}: {tag}", file=sys.stderr)
        print(f"  原文保留: MCP/Skill 内容未脱敏、未执行零明文扫描", file=sys.stderr)

        if args.upload_url:
            ok, msg = upload(zip_path, args.upload_url)
            print(f"  upload: {'ok' if ok else 'FAILED'} - {msg}", file=sys.stderr)
            if not ok:
                return 3
        return 0


def _write_zip_with_symlinks(staging: Path, zip_path: Path) -> None:
    """Write files, directories (including empty ones), and symlinks to ZIP."""
    import zipfile
    base = staging.parent
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(staging, followlinks=False):
            root_path = Path(root)
            for d in list(sorted(dirs)):
                if _is_python_runtime_cache_name(d):
                    dirs.remove(d)
                    continue
                dp = root_path / d
                arc = dp.relative_to(base).as_posix()
                if dp.is_symlink():
                    _zip_add_symlink(zf, dp, arc)
                    dirs.remove(d)
                else:
                    _zip_add_dir(zf, dp, arc + "/")
            for fn in sorted(files):
                if _is_python_runtime_cache_name(fn):
                    continue
                fp = root_path / fn
                arc = fp.relative_to(base).as_posix()
                if fp.is_symlink():
                    _zip_add_symlink(zf, fp, arc)
                else:
                    zf.write(fp, arc)


def _zip_add_dir(zf, directory: Path, arcname: str) -> None:
    import zipfile
    info = zipfile.ZipInfo(arcname if arcname.endswith("/") else arcname + "/")
    st = directory.stat()
    info.external_attr = (st.st_mode & 0xFFFF) << 16
    info.external_attr |= 0x10  # DOS directory bit
    info.compress_type = zipfile.ZIP_STORED
    zf.writestr(info, b"")


def _zip_add_symlink(zf, link_path: Path, arcname: str) -> None:
    """把 symlink 写进 zip：external_attr 高 16 位 = st_mode，body = target."""
    import zipfile
    target = os.readlink(link_path).encode("utf-8")
    st = os.lstat(link_path)
    info = zipfile.ZipInfo(arcname)
    # external_attr 高 16 位存 Unix st_mode。symlink mode = 0o12xxxx (S_IFLNK)。
    info.external_attr = (st.st_mode & 0xFFFF) << 16
    # 也写低 16 位（DOS attrs）= 0，不影响
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, target)


def _guess_openclaw_version() -> str:
    # 不调用 openclaw 二进制(会拉起整个运行时/网关,可能挂起)。读 openclaw.json meta。
    for cand in (
        os.path.expanduser("~/.openclaw/openclaw.json"),
        os.path.join(os.environ.get("OPENCLAW_HOME", ""), "openclaw.json") if os.environ.get("OPENCLAW_HOME") else "",
    ):
        if not cand or not os.path.isfile(cand):
            continue
        try:
            meta = json.loads(Path(cand).read_text(encoding="utf-8")).get("meta", {})
            v = meta.get("lastTouchedVersion")
            if v:
                return str(v)
        except Exception:
            pass
    return "unknown"


if __name__ == "__main__":
    sys.exit(main())
