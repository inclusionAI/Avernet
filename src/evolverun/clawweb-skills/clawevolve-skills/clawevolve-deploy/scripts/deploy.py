#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deploy.py — agent 镜像部署核心（clawevolve-deploy）。

把 clawevolve-pack 产出的镜像铺到目标 bot workspace。md/mcp 按物料层恢复，
Skill 层忠实恢复 workspace/skills/ 与 workspace/skills-local/ 两个目录快照。
软链只按 readlink target 重建，不跟随、不解析、不要求目标存在；归档和目标
parent 均执行路径安全与 no-follow 校验。Release 管理的 clawevolve-*、
clawbench-*、ocb-* entry 在清理、恢复和校验中受到保护。
仅依赖 Python 标准库。
"""
# 最低 Python 版本: 3.9（使用了 Path.is_relative_to / str.removesuffix 等）
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 3
SUPPORTED_PACK_SCHEMAS = {1, 2, 3}
DEFAULT_EVOLVE_RESULTS_BASE = "/home/admin/.openclaw/workspace/clawevolve_results"
RELEASE_MANAGED_SKILL_PREFIXES = ("clawevolve-", "clawbench-", "ocb-")
READONLY_SKILL_ENTRY_NAMES = {"skills-repo", "skills-center"}


def _is_release_managed_skill_name(name: str) -> bool:
    """ClawEvolve release assets survive Bot Pack deploy and restore."""
    value = str(name or "")
    candidate = value[1:] if value.startswith(".") else value
    candidate = candidate.split(".backup.", 1)[0]
    return candidate.startswith(RELEASE_MANAGED_SKILL_PREFIXES)

# MD 集合不再硬编码：deploy 从 manifest 实际 md 层 names 读，与 pack 产出对齐。
# 仅作为 manifest 缺失时的兜底 fallback（不应触发）。
MD_SET_FALLBACK = {
    "AGENTS.md", "BOOTSTRAP.md", "IDENTITY.md", "SOUL.md", "TOOLS.md", "USER.md",
    "RULES.md", "OKR.md",
}

# 预留扩展开关：与 pack 对齐，命中即报待 patch
RESERVED_WITH_FLAGS = {
    "with-model": "模型 id+参数还原 —— 待 patch，优先级最高",
    "with-memory": "memory/ 与 MEMORY.md 还原 —— 待 patch",
    "with-plugins": "插件/扩展 + 运行时旋钮还原 —— 待 patch",
    "with-activeset": "活跃技能集元数据还原 —— 待 patch",
    "with-identity": "device identity 还原（跨机不推荐）—— 待 patch",
    "with-sessions": "会话历史 .jsonl 还原 —— 待 patch",
}


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def rel_sha256_and_items(base: Path) -> tuple[str | None, list[tuple[str, str]]]:
    """计算 base 目录的相对路径哈希树（已排序），返回 (合并 sha256, [(path, sha256), ...])。"""
    if not base.is_dir():
        return None, []
    items = []
    paths = sorted(base.rglob("*"))
    h = hashlib.sha256()
    for it in paths:
        rel = str(it.relative_to(base))
        if it.is_symlink():
            h.update(f"L:{rel}:{os.readlink(it)}\n".encode("utf-8"))
            items.append((rel, "L:" + os.readlink(it)))
        elif it.is_file():
            h.update(f"F:{rel}:{sha256_file(it)}\n".encode("utf-8"))
            items.append((rel, sha256_file(it)[:16]))
    return h.hexdigest(), items


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


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


def resolve_evolve_results_dir(args, manifest: dict | None = None) -> Path | None:
    """Resolve target evolve_results/<run-id> without scanning global state.

    Priority mirrors clawevolve-pack where possible, then falls back to the packaged
    layer's original source basename so deploy(pack(run_id=X)) restores X.
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
    src = None
    if manifest:
        src = (manifest.get("clawevolve_results") or {}).get("source")
    if src:
        name = Path(src).name
        if name and name not in {"clawevolve_results", ".", "/"}:
            return Path(DEFAULT_EVOLVE_RESULTS_BASE) / name
    return None


def download_image(url: str, dest_dir: Path) -> Path:
    """Download image from a clawweb/OSS signed URL to a temp file.

    No OSS SDK is required in the skill; clawweb should provide a downloadable URL
    or proxy endpoint.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "agent-image.zip"
    if not re.search(r"\.(zip|tgz|tar\.gz)$", name, re.I):
        name = "agent-image.zip"
    out = dest_dir / name
    if shutil.which("curl"):
        import subprocess
        r = subprocess.run(["curl", "-fL", "-sS", "-o", str(out), url], text=True, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"curl 下载失败: {r.stderr.strip()[:300]}")
        return out
    import urllib.request
    with urllib.request.urlopen(url, timeout=300) as resp, out.open("wb") as f:
        shutil.copyfileobj(resp, f)
    return out


# ⚠️ 此函数用正则解析 pack.py 的 to_yaml 输出，强耦合于 pack 的固定输出格式。
# 若 pack 的 to_yaml 输出格式变化（缩进、引号、字段顺序），此解析可能静默失败。
# TODO: 考虑在 manifest 中嵌入 JSON 副本来解耦（向后兼容的增量改动）。
def parse_manifest_from_staging(staging: Path) -> dict:
    """读 package/agent.image.yaml。极简 YAML parser 不足以通用，但 pack 产出格式固定，
    这里用 json fallback + 关键字段正则提取（够用且稳）。"""
    yml = (staging / "agent.image.yaml")
    if not yml.is_file():
        raise RuntimeError("镜像缺 package/agent.image.yaml，不是 clawevolve-pack 产出？")
    text = yml.read_text(encoding="utf-8")
    # 解析顶层字段（pack to_yaml 输出格式：单行 `key: value` 或块）
    m = {}
    # 顶层标量
    for key in ("id", "version", "title", "description"):
        mm = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.M)
        if mm:
            v = mm.group(1).strip()
            if v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            m[key] = v
    # source.contentDigest / contentSha8（pack 写入；deploy 用它跟文件名 sha8 比对，防传输损坏/版本错位）
    m["contentDigest"] = None
    m["contentSha8"] = None
    schema = re.search(r"^  schemaVersion:\s*(\d+)\s*$", text, re.M)
    m["schemaVersion"] = int(schema.group(1)) if schema else None
    cd = re.search(r"^    contentDigest:\s*([0-9a-fA-F]+)\s*$", text, re.M)
    if cd:
        m["contentDigest"] = cd.group(1).strip()
    cs8 = re.search(r"^    contentSha8:\s*([0-9a-fA-F]+)\s*$", text, re.M)
    if cs8:
        m["contentSha8"] = cs8.group(1).strip()
    # layers 是列表，pack 固定 md/mcp/skill 三项；不强解析各 layer，校验阶段直接看目录。
    m["_raw"] = text
    m["layers"] = []
    # 粗略提取 layer name 列表（`  - name: <x>`）
    for mm in re.finditer(r"^  - name:\s*(\S+)", text, re.M):
        m["layers"].append(mm.group(1))
    # 提取 md 层的 names 列表（`    names:` 块列表，仅 md layer 有此字段）
    m["md_names"] = []
    md_block = _extract_layer_block(text, "md")
    if md_block:
        nm = re.search(r"^    names:\s*\n((?:      - .+\n)*)", md_block, re.M)
        if nm:
            m["md_names"] = [ln.strip().lstrip("- ").strip().strip('"')
                             for ln in nm.group(1).splitlines()]
    # evolve_results layer 元数据（与 clawevolve-pack 对齐）：用于默认恢复 run id。
    evo_block = _extract_layer_block(text, "clawevolve_results")
    m["clawevolve_results"] = {"present": bool(evo_block), "skipped": True, "source": None}
    if evo_block:
        m["clawevolve_results"]["skipped"] = bool(re.search(r"^    skipped:\s*true\s*$", evo_block, re.M))
        sm = re.search(r"^    source:\s*(.+?)\s*$", evo_block, re.M)
        if sm:
            src = sm.group(1).strip().strip('"').strip("'")
            m["clawevolve_results"]["source"] = src
    skill_block = _extract_layer_block(text, "skill")
    skill_sha = re.search(r"^    sha256:\s*(\S+)\s*$", skill_block, re.M)
    m["skillSha256"] = skill_sha.group(1).strip().strip('"').strip("'") if skill_sha else None
    digest_algorithm = re.search(r"^      digestAlgorithm:\s*(\S+)\s*$", skill_block, re.M)
    m["skillDigestAlgorithm"] = (
        digest_algorithm.group(1).strip().strip('"').strip("'") if digest_algorithm else None
    )
    m["privateSkillItems"] = []
    private_items = re.search(
        r"^    privateSkillItems:\s*\n([\s\S]*?)(?=^    [A-Za-z_][A-Za-z0-9_]*:|\Z)",
        skill_block,
        re.M,
    )
    if private_items:
        current = None
        for line in private_items.group(1).splitlines():
            item_start = re.match(r"^      - name:\s*(.+?)\s*$", line)
            if item_start:
                current = {"name": item_start.group(1).strip().strip('"').strip("'")}
                m["privateSkillItems"].append(current)
                continue
            field = re.match(r"^        (layout|activation|linkTarget|packageRoot|activationStatus):\s*(.+?)\s*$", line)
            if field and current is not None:
                current[field.group(1)] = field.group(2).strip().strip('"').strip("'")
    return m


# ─── 第一段：校验镜像 ─────────────────────────────────────────────────────────

def _extract_image(image_path: Path, dest: Path) -> None:
    """解压镜像。支持 .zip（默认格式）和 .tgz/.tar.gz。
    zip 分支需要手动重建 symlink（zipfile.extractall 把 symlink body 当文件内容写）：
      扫 ZipInfo.external_attr 高 16 位的 Unix st_mode，若 S_IFLNK(0o120000) 置位，
      先删掉 extractall 误写的「文件」，再用 readlink body 重建 symlink。
    tgz 分支 tarfile 原生保留 symlink，但 PEP 706 (Py3.12+) 默认 filter='data' 拒绝
    绝对路径软链 → 用 filter='tar'（允许绝对路径软链，过滤其它危险模式）。
    """
    low = image_path.name.lower()
    if low.endswith('.zip') or zipfile.is_zipfile(image_path):
        _extract_zip_with_symlinks(image_path, dest)
    elif low.endswith(('.tgz', '.tar.gz')) or tarfile.is_tarfile(image_path):
        mode = "r:gz" if low.endswith(('.tgz', '.gz')) else "r:"
        with tarfile.open(image_path, mode) as tar:
            _safe_extractall(tar, dest)
    else:
        raise RuntimeError("不支持的镜像格式（期望 .zip / .tgz / .tar.gz）")


def _archive_rel_path(name: str) -> Path:
    normalized = str(name or "").replace("\\", "/")
    path = Path(normalized)
    if (not normalized or path.is_absolute() or re.match(r"^[A-Za-z]:/", normalized) or
            any(part in {"", ".", ".."} for part in path.parts)):
        raise RuntimeError(f"非法归档路径: {name!r}")
    return path


def _validate_archive_entries(names: list[str]) -> None:
    paths = [_archive_rel_path(name.rstrip("/") or name) for name in names]
    seen = set()
    for path in paths:
        key = path.as_posix()
        if key in seen:
            raise RuntimeError(f"归档存在重复 entry: {key}")
        seen.add(key)
    # ZIP symlink-parent validation needs mode metadata and is completed by
    # _extract_zip_with_symlinks after this path-only validation.


def _safe_mkdir_parent(path: Path, root: Path) -> None:
    rel = path.relative_to(root)
    current = root
    for part in rel.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                raise RuntimeError(f"归档 parent 非普通目录: {current}")
        else:
            current.mkdir()


def _extract_zip_with_symlinks(zip_path: Path, dest: Path) -> None:
    """Safely extract ZIP files, preserving files, directories and symlinks."""
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        _validate_archive_entries([i.filename for i in infos])
        symlink_entries = {}
        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                symlink_entries[_archive_rel_path(info.filename).as_posix()] = info
        for info in infos:
            rel = _archive_rel_path(info.filename)
            rel_key = rel.as_posix().rstrip("/")
            if any(rel_key == parent or rel_key.startswith(parent + "/") for parent in symlink_entries if parent != rel_key):
                raise RuntimeError(f"归档软链 parent 冲突: {info.filename}")
            target = dest / rel
            _safe_mkdir_parent(target.parent, dest)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                if target.exists() or target.is_symlink():
                    raise RuntimeError(f"归档 entry 冲突: {info.filename}")
                link_target = zf.read(info).decode("utf-8")
                os.symlink(link_target, target)
            elif info.filename.endswith("/") or mode == 0o040000:
                if target.exists() or target.is_symlink():
                    if not target.is_dir() or target.is_symlink():
                        raise RuntimeError(f"归档目录 entry 冲突: {info.filename}")
                else:
                    target.mkdir()
            else:
                if target.exists() or target.is_symlink():
                    raise RuntimeError(f"归档 entry 冲突: {info.filename}")
                with zf.open(info) as src, target.open("xb") as out:
                    shutil.copyfileobj(src, out)
                archived_mode = (info.external_attr >> 16) & 0o777
                if archived_mode:
                    os.chmod(target, archived_mode)




def _skill_snapshot_root_states(staging: Path, manifest: dict) -> dict[str, bool]:
    """Read snapshot root presence without making legacy metadata a restore gate.

    Only the versioned ``skill-snapshot-v1`` contract makes ``present``
    authoritative. Older v1/v2 and early-v3 packs were produced by several
    writers, so their optional snapshot metadata may be absent or stale. For
    those packs the archive tree is the restore source of truth.
    """
    raw = str(manifest.get("_raw") or "")
    skill_block = _extract_layer_block(raw, "skill") if raw else ""
    states: dict[str, bool] = {}
    for root in ("skills", "skills-local"):
        escaped = re.escape(root)
        match = re.search(
            rf"-\s+path:\s*{escaped}/?\s*\n\s*present:\s*(true|false)",
            skill_block, re.I,
        )
        actual_path = staging / root
        actual = actual_path.is_dir() and not actual_path.is_symlink()
        if match and manifest.get("skillDigestAlgorithm") == "personal-skill-snapshot-v1":
            declared = match.group(1).lower() == "true"
            if declared != actual:
                raise RuntimeError(
                    f"Skill snapshot root present 与包内目录不一致: {root}: manifest={declared}, actual={actual}"
                )
            states[root] = declared
            continue
        states[root] = actual
    return states


def _skill_snapshot_sha256(staging: Path) -> str:
    """Recompute the schema-v3 joint digest without following symlinks."""
    digest = hashlib.sha256()
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
                    dirnames.remove(name)
                else:
                    digest.update(f"D\0{rel}\0".encode())
            for name in sorted(filenames):
                path = base / name
                rel = path.relative_to(staging).as_posix()
                if path.is_symlink():
                    digest.update(f"L\0{rel}\0{os.readlink(path)}\0".encode())
                else:
                    digest.update(f"F\0{rel}\0{sha256_file(path)}\0".encode())
    return digest.hexdigest()


def _validate_skill_snapshot(staging: Path, manifest: dict) -> None:
    states = _skill_snapshot_root_states(staging, manifest)
    for root, present in states.items():
        path = staging / root
        if present and (path.is_symlink() or not path.is_dir()):
            raise RuntimeError(f"Skill snapshot root 非普通目录: package/{root}")
        if not present and (path.exists() or path.is_symlink()):
            raise RuntimeError(f"Skill snapshot absent root 仍存在: package/{root}")
    # A package directory entry must never be a symlink root.
    for root in ("skills", "skills-local"):
        path = staging / root
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise RuntimeError(f"Skill snapshot root 类型非法: {path}")
    # Early schema-v3 builds used a different, unversioned Skill digest.  They are
    # valid historical Packs, so enforce the joint digest only when the Pack
    # explicitly declares the algorithm introduced by the dual-root contract.
    if (manifest.get("schemaVersion") == 3 and
            manifest.get("skillDigestAlgorithm") == "personal-skill-snapshot-v1" and
            manifest.get("skillSha256")):
        actual = _skill_snapshot_sha256(staging)
        if actual != manifest["skillSha256"]:
            raise RuntimeError(
                f"Skill snapshot 摘要不一致: manifest={manifest['skillSha256']}, actual={actual}"
            )


def _remove_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        _rmtree_nfs_safe(path)
    elif path.exists():
        raise RuntimeError(f"不支持删除的文件类型: {path}")


def _assert_plain_dir(path: Path, label: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise RuntimeError(f"{label} 必须是不存在或普通目录: {path}")


def _release_entry_at(root_name: str, rel: Path) -> bool:
    """Whether *rel* is a protected Release-managed root entry."""
    parts = rel.parts
    if not parts:
        return False
    if root_name == "skills-local":
        return len(parts) == 1 and _is_release_managed_skill_name(parts[0])
    if root_name == "skills":
        return ((len(parts) == 1 and _is_release_managed_skill_name(parts[0])) or
                (len(parts) == 2 and parts[0] == "skills-local" and
                 _is_release_managed_skill_name(parts[1])))
    return False


def _is_personal_skill_link_target(target: str) -> bool:
    """Whether a link syntactically targets nested or sibling personal Skills."""
    value = str(target or "").replace("\\", "/").rstrip("/")
    if not value:
        return False
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if len(parts) >= 2 and parts[0] == "skills-local":
        return True
    if len(parts) >= 3 and parts[0] == ".." and parts[1] == "skills-local":
        return True
    return "/workspace/skills/skills-local/" in value or "/workspace/skills-local/" in value


def _is_readonly_skill_entry(root_name: str, rel: Path, path: Path) -> bool:
    """Public/shared Skill entries are environment state, not Bot snapshot state."""
    if root_name != "skills" or len(rel.parts) != 1:
        return False
    if rel.name in READONLY_SKILL_ENTRY_NAMES:
        return True
    return path.is_symlink() and not _is_personal_skill_link_target(os.readlink(path))


def _clear_snapshot_root(root: Path, root_name: str) -> list[str]:
    """Remove personal snapshot content while preserving public/Release entries."""
    removed: list[str] = []
    if not (root.exists() or root.is_symlink()):
        return removed
    _assert_plain_dir(root, f"目标 {root_name} root")

    def clear_dir(directory: Path, rel: Path) -> None:
        for entry in sorted(directory.iterdir(), key=lambda x: x.name):
            entry_rel = rel / entry.name
            if _release_entry_at(root_name, entry_rel):
                continue
            _remove_entry(entry)
            removed.append(entry_rel.as_posix())

    # The nested skills/skills-local directory is itself ordinary snapshot data;
    # preserve its protected children while deleting every other child.
    for entry in sorted(root.iterdir(), key=lambda x: x.name):
        rel = Path(entry.name)
        if _release_entry_at(root_name, rel):
            continue
        if _is_readonly_skill_entry(root_name, rel, entry):
            continue
        if root_name == "skills" and entry.name == "skills-local" and entry.is_dir() and not entry.is_symlink():
            clear_dir(entry, rel)
            if not any(entry.iterdir()):
                entry.rmdir()
                removed.append(rel.as_posix())
            continue
        _remove_entry(entry)
        removed.append(rel.as_posix())
    return removed


def _ensure_nofollow_parent(parent: Path, root: Path, force_overwrite: bool) -> None:
    rel = parent.relative_to(root)
    current = root
    for part in rel.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink() or not current.is_dir():
                if not force_overwrite:
                    raise RuntimeError(f"目标 parent 冲突（禁止跟随）: {current}")
                _remove_entry(current)
                current.mkdir()
        else:
            current.mkdir()


def _copy_snapshot_tree(source: Path, target_root: Path, root_name: str, force_overwrite: bool) -> dict:
    result = {"files": 0, "directories": 0, "symlinks": 0, "items": 0, "skipped": []}
    if not source.is_dir() or source.is_symlink():
        return result

    def copy_entry(src: Path, dst: Path, rel: Path) -> None:
        if _is_deploy_noise_name(src.name):
            result["skipped"].append(rel.as_posix())
            return
        if _release_entry_at(root_name, rel):
            result["skipped"].append(rel.as_posix())
            return
        if _is_readonly_skill_entry(root_name, rel, src):
            result["skipped"].append(rel.as_posix())
            return
        # A current public/shared activation belongs to the environment. An old
        # Pack must not replace it even if that Pack contains a same-name entry.
        if (dst.exists() or dst.is_symlink()) and _is_readonly_skill_entry(root_name, rel, dst):
            result["skipped"].append(f"{rel.as_posix()}(current read-only Skill)")
            return
        _ensure_nofollow_parent(dst.parent, target_root, force_overwrite)
        if src.is_symlink():
            if dst.exists() or dst.is_symlink():
                if not force_overwrite:
                    raise RuntimeError(f"目标 {dst} 已存在（用 --force-overwrite 覆盖）")
                _remove_entry(dst)
            os.symlink(os.readlink(src), dst)
            result["symlinks"] += 1
        elif src.is_dir():
            if dst.exists() or dst.is_symlink():
                if dst.is_symlink() or not dst.is_dir():
                    if not force_overwrite:
                        raise RuntimeError(f"目标 {dst} 类型冲突")
                    _remove_entry(dst)
                    dst.mkdir()
            else:
                dst.mkdir()
            result["directories"] += 1
            for child in sorted(src.iterdir(), key=lambda x: x.name):
                copy_entry(child, dst / child.name, rel / child.name)
        elif src.is_file():
            if dst.exists() or dst.is_symlink():
                if not force_overwrite:
                    raise RuntimeError(f"目标 {dst} 已存在（用 --force-overwrite 覆盖）")
                _remove_entry(dst)
            shutil.copy2(src, dst, follow_symlinks=False)
            result["files"] += 1
        else:
            raise RuntimeError(f"不支持的 Skill snapshot 文件类型: {src}")
        result["items"] += 1

    for child in sorted(source.iterdir(), key=lambda x: x.name):
        copy_entry(child, target_root / child.name, Path(child.name))
    return result


def _root_has_protected_entries(root: Path, root_name: str) -> bool:
    if not root.is_dir() or root.is_symlink():
        return False
    if any(_release_entry_at(root_name, Path(e.name)) for e in root.iterdir()):
        return True
    if any(_is_readonly_skill_entry(root_name, Path(e.name), e) for e in root.iterdir()):
        return True
    # For skills/, a protected nested entry may require retaining the
    # skills-local parent even when the top-level root itself is absent.
    nested = root / "skills-local"
    return (root_name == "skills" and nested.is_dir() and not nested.is_symlink() and
            any(_is_release_managed_skill_name(e.name) for e in nested.iterdir()))


def _empty_stats() -> dict:
    return {"items": 0, "files": 0, "directories": 0, "symlinks": 0, "skipped": []}


def _apply_skill_snapshot(staging: Path, ws: Path, manifest: dict, force_overwrite: bool) -> dict:
    states = _skill_snapshot_root_states(staging, manifest)
    result = {"roots": {}, "skills_top": [], "skills_local": [], "skipped_obsolete_skill_links": []}
    ws.mkdir(parents=True, exist_ok=True)
    for root_name in ("skills", "skills-local"):
        src = staging / root_name
        dst = ws / root_name
        if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
            if not force_overwrite:
                raise RuntimeError(f"目标 {root_name} root 类型冲突（禁止跟随）: {dst}")
            _remove_entry(dst)
        if dst.exists():
            _clear_snapshot_root(dst, root_name)
        present = states[root_name]
        if present:
            if not dst.exists():
                dst.mkdir()
            stats = _copy_snapshot_tree(src, dst, root_name, force_overwrite)
        else:
            if dst.exists() or dst.is_symlink():
                if _root_has_protected_entries(dst, root_name):
                    stats = _empty_stats()
                else:
                    _remove_entry(dst)
                    stats = _empty_stats()
            else:
                stats = _empty_stats()
        result["roots"][root_name] = {"present": present, **stats}
        result["skipped_obsolete_skill_links"].extend(stats.get("skipped", []))
    return result


def _safe_extractall(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract tar entries without following archive or destination symlinks."""
    members = tar.getmembers()
    names = [m.name for m in members]
    _validate_archive_entries(names)
    symlinks = {
        _archive_rel_path(m.name.rstrip("/") or m.name).as_posix()
        for m in members if m.issym()
    }
    dest.mkdir(parents=True, exist_ok=True)
    for member in members:
        rel = _archive_rel_path(member.name.rstrip("/") or member.name)
        key = rel.as_posix()
        if any(key != parent and key.startswith(parent + "/") for parent in symlinks):
            raise RuntimeError(f"归档软链 parent 冲突: {member.name}")
        target = dest / rel
        _safe_mkdir_parent(target.parent, dest)
        if member.isdir():
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_dir():
                    raise RuntimeError(f"归档目录 entry 冲突: {member.name}")
            else:
                target.mkdir()
            continue
        if member.issym():
            if target.exists() or target.is_symlink():
                raise RuntimeError(f"归档 entry 冲突: {member.name}")
            os.symlink(member.linkname, target)
            continue
        if member.isreg():
            if target.exists() or target.is_symlink():
                raise RuntimeError(f"归档 entry 冲突: {member.name}")
            source = tar.extractfile(member)
            if source is None:
                raise RuntimeError(f"无法读取归档文件: {member.name}")
            with source, target.open("xb") as out:
                shutil.copyfileobj(source, out)
            try:
                os.chmod(target, member.mode & 0o7777)
            except OSError:
                pass
            continue
        raise RuntimeError(f"不支持的归档 entry 类型: {member.name}")


def validate_image(image_path: Path) -> tuple[Path, dict]:
    """解包镜像到临时 staging，校验 manifest 与文件名 sha8 一致性。
    支持 .tgz/.tar.gz 和 .zip 格式。返回 (staging, manifest)。
    sha8 校验语义：pack 文件名内嵌的 sha8 = content_digest[:8]（各 layer
    name+sha256 摘要的前 8 位），不是 tar 文件本身 sha256。故必须解包读
    manifest.source.contentDigest 才能跟文件名 sha8 比对，防传输损坏/版本错位。"""
    if not image_path.is_file():
        raise RuntimeError(f"镜像文件不存在: {image_path}")
    staging_root = Path(tempfile.mkdtemp(prefix="deploy-"))
    _extract_image(image_path, staging_root)
    staging = staging_root / "package"
    if not staging.is_dir():
        raise RuntimeError(f"镜像结构异常：缺 package/ 顶层（{staging}）")
    manifest = parse_manifest_from_staging(staging)
    if manifest.get("schemaVersion") not in SUPPORTED_PACK_SCHEMAS:
        raise RuntimeError(f"Pack/Deploy schema 不兼容: pack={manifest.get('schemaVersion')!r}, supported={sorted(SUPPORTED_PACK_SCHEMAS)}")
    if not manifest.get("layers"):
        raise RuntimeError("manifest 缺 layers，疑似不是 clawevolve-pack 产出")
    for sub in ("md", "skills"):
        if not (staging / sub).is_dir():
            if sub == "md":
                raise RuntimeError(f"镜像缺 package/{sub}/")
    _validate_skill_snapshot(staging, manifest)
    # 文件名 sha8 vs manifest.contentDigest 一致性校验（防传输损坏/版本错位）
    fn_sha_m = re.search(r"__([0-9a-f]{8})\.(?:tgz|tar\.gz|zip)$", image_path.name, re.I)
    if fn_sha_m:
        fn_sha8 = fn_sha_m.group(1).lower()
        cd = manifest.get("contentDigest")
        cs8 = manifest.get("contentSha8")
        # 优先用 manifest.contentSha8 字段（pack 新版写入），fallback 用 contentDigest[:8]
        expect_sha8 = (cs8 or (cd[:8] if cd else None))
        if expect_sha8 and fn_sha8 != expect_sha8.lower():
            raise RuntimeError(
                f"镜像 sha8 不一致（文件名={fn_sha8}，manifest={expect_sha8.lower()}）"
                f" —— 传输可能损坏/篡改/版本错位")
    return staging, manifest


# ─── 第二段：备份 + 铺入 ──────────────────────────────────────────────────────



def _is_deploy_noise_name(name: str) -> bool:
    """Only unstable NFS orphan handles are omitted during restore."""
    return str(name or "").startswith(".nfs")


def _is_nfs_temp_path(path: Path) -> bool:
    """Return True for NFS silly-renamed temp files such as .nfsXXXX.

    These files can appear when a deleted file is still held open by another
    process. They are inherently transient and may disappear between iterdir()
    and stat()/open(), so backup/cleanup code must not treat them as fatal.
    """
    return any(part.startswith(".nfs") for part in path.parts)


def _is_transient_fs_error(exc: BaseException) -> bool:
    """Whether an OS error is safe to ignore while snapshotting a live workspace."""
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, OSError):
        return getattr(exc, "errno", None) in {
            errno.ENOENT,       # disappeared between scan and open
            errno.ENOTDIR,      # parent shape changed concurrently
            errno.ESTALE,       # stale NFS file handle (platform dependent)
            errno.EBUSY,        # .nfs file still held open
        }
    return False


def _tar_add_live_tree(tar: tarfile.TarFile, src: Path, arcname: str,
                       exclude_arc=None) -> list[str]:
    """Add a live workspace path to tar, tolerating transient NFS churn.

    ``tarfile.add(..., recursive=True)`` aborts the entire backup if a child file
    disappears or a NFS silly-renamed ``.nfs*`` handle is encountered. During an
    evolution restore there may still be benchmark/agent processes holding files
    open under ``workspace/skills``. For backup purposes those ``.nfs*`` files
    are not meaningful state, so we skip them and continue.

    Returns a list of skipped path descriptions for optional diagnostics.
    """
    skipped: list[str] = []

    def add_one(path: Path, arc: str) -> None:
        if exclude_arc is not None and exclude_arc(arc):
            skipped.append(f"excluded:{arc}")
            return
        if _is_nfs_temp_path(path):
            skipped.append(f"nfs-temp:{path}")
            return
        try:
            # Add the entry itself without recursing; this preserves symlinks as
            # symlinks because TarFile.dereference defaults to False.
            tar.add(path, arcname=arc, recursive=False)
        except Exception as exc:
            if _is_transient_fs_error(exc):
                skipped.append(f"transient:{path}: {exc}")
                return
            raise
        try:
            is_dir = path.is_dir() and not path.is_symlink()
        except OSError as exc:
            if _is_transient_fs_error(exc):
                skipped.append(f"transient-stat:{path}: {exc}")
                return
            raise
        if not is_dir:
            return
        try:
            children = sorted(path.iterdir())
        except OSError as exc:
            if _is_transient_fs_error(exc):
                skipped.append(f"transient-list:{path}: {exc}")
                return
            raise
        for child in children:
            add_one(child, f"{arc}/{child.name}")

    add_one(src, arcname)
    return skipped

def _list_top_entries(p: Path) -> list[Path]:
    if not p.is_dir():
        return []
    return sorted(p.iterdir())


def backup_workspace(ws: Path, md_set: set[str], backup_dir: Path) -> Path | None:
    """Backup personal Skill state plus md/mcp, preserving links without public assets."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_tgz = backup_dir / f"{ts}.workspace.bak.tgz"
    items: list[Path] = []
    for root_name in ("skills", "skills-local"):
        root = ws / root_name
        if root.exists() or root.is_symlink():
            items.append(root)
    for md in sorted(md_set):
        f = ws / md
        if f.exists() or f.is_symlink():
            items.append(f)
    for mcp_path in (ws / "config" / "mcporter.json", ws / ".mcporter.json"):
        if mcp_path.is_file() or mcp_path.is_symlink():
            items.append(mcp_path)
    if not items:
        return None
    skipped: list[str] = []
    with tarfile.open(bak_tgz, "w:gz") as tar:
        for item in items:
            arcname = str(item.relative_to(ws)) if item.is_relative_to(ws) else item.name
            if arcname in {"skills", "skills-local"}:
                root_name = arcname

                def exclude_release(arc: str, root_name=root_name) -> bool:
                    parts = Path(arc).parts
                    rel = Path(*parts[1:]) if len(parts) > 1 else Path()
                    if _release_entry_at(root_name, rel):
                        return True
                    candidate = ws / arc
                    return _is_readonly_skill_entry(root_name, rel, candidate)

                skipped.extend(_tar_add_live_tree(tar, item, arcname, exclude_release))
            else:
                skipped.extend(_tar_add_live_tree(tar, item, arcname))
    if skipped:
        sidecar = bak_tgz.with_suffix(bak_tgz.suffix + ".skipped.json")
        try:
            sidecar.write_text(json.dumps({"skipped": skipped[:200], "total": len(skipped)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    return bak_tgz


def _rmtree_nfs_safe(path: Path) -> bool:
    """递归删除目录，容忍 NFS stale handle (.nfs*) 文件残留。
    NFS 环境下删除文件后，若有进程仍持有句柄，NFS 会把文件改名成 .nfsXXXXXX
    保留在目录中，导致 shutil.rmtree 报 [Errno 39] Directory not empty 或
    [Errno 16] Device or resource busy。
    返回 True 表示目录已删除（或已不存在），False 表示目录壳仍在（含 .nfs* 残留）。"""
    if not path.exists() and not path.is_symlink():
        return True
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    # 目录：先递归删非 .nfs* 内容
    for child in sorted(path.iterdir()):
        if child.name.startswith('.nfs'):
            continue
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                _rmtree_nfs_safe(child)
        except OSError:
            pass
    # 尝试删 .nfs* 残留（句柄可能已释放）
    for child in sorted(path.iterdir()):
        if child.name.startswith('.nfs'):
            try:
                child.unlink()
            except OSError:
                pass
    # 删目录本身
    try:
        path.rmdir()
        return True
    except OSError:
        return False

def _clear_skills_entries(skills_dir: Path) -> list[str]:
    """Legacy wrapper retained for rollback callers; clears both snapshot roots."""
    ws = skills_dir.parent
    removed = []
    removed.extend(_clear_snapshot_root(skills_dir, "skills"))
    removed.extend(_clear_snapshot_root(ws / "skills-local", "skills-local"))
    return removed


def _clear_md(ws: Path, md_set: set[str]) -> list[str]:
    removed = []
    for md in sorted(md_set):
        f = ws / md
        if f.exists() or f.is_symlink():
            try:
                f.unlink()
                removed.append(md)
            except OSError as e:
                raise RuntimeError(f"清空 {md} 失败: {e}")
    return removed


def _clear_mcp(ws: Path) -> list[str]:
    removed = []
    for p in (ws / "config" / "mcporter.json", ws / ".mcporter.json"):
        if p.is_file() or p.is_symlink():
            try:
                p.unlink()
                removed.append(str(p.relative_to(ws)))
            except OSError as e:
                raise RuntimeError(f"清空 {p} 失败: {e}")
    return removed


def laydown(staging: Path, ws: Path, md_set: set[str], force_overwrite: bool,
            manifest: dict | None = None) -> dict:
    """Restore md/mcp and the two Skill snapshot roots without following links."""
    manifest = manifest or {}
    result = {"md": [], "mcp": None, "skills_top": [], "skills_local": [],
              "skipped_obsolete_skill_links": [], "clawevolve_results": None}
    src_md = staging / "md"
    if src_md.is_dir():
        for src in sorted(src_md.glob("*.md")):
            dst = ws / src.name
            if dst.exists() or dst.is_symlink():
                if not force_overwrite: raise RuntimeError(f"目标 {dst} 已存在（用 --force-overwrite 覆盖）")
                _remove_entry(dst)
            shutil.copy2(src, dst)
            result["md"].append(src.name)
    src_mcp = staging / "mcp" / "mcporter.json"
    if src_mcp.is_file():
        dst_dir = ws / "config"
        _assert_plain_dir(dst_dir, "目标 config")
        if not dst_dir.exists(): dst_dir.mkdir()
        dst = dst_dir / "mcporter.json"
        if dst.exists() or dst.is_symlink():
            if not force_overwrite: raise RuntimeError(f"目标 {dst} 已存在（用 --force-overwrite 覆盖）")
            _remove_entry(dst)
        shutil.copy2(src_mcp, dst)
        result["mcp"] = str(dst.relative_to(ws))
    skill_result = _apply_skill_snapshot(staging, ws, manifest, force_overwrite)
    result["skills_top"] = [x.name for x in (ws / "skills").iterdir()] if (ws / "skills").is_dir() else []
    result["skills_local"] = [x.name for x in (ws / "skills-local").iterdir()] if (ws / "skills-local").is_dir() else []
    result["skipped_obsolete_skill_links"] = skill_result["skipped_obsolete_skill_links"]
    result["snapshot"] = skill_result
    return result


def laydown_evolve_results(staging: Path, target_dir: Path, force_overwrite: bool) -> dict:
    """Restore package/clawevolve_results/ to the bound run directory.

    The package path contains the *contents* of one run dir, so target_dir should be
    /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>.
    """
    src = staging / "clawevolve_results"
    if not src.is_dir():
        return {"restored": False, "reason": "package/clawevolve_results missing"}
    if target_dir.exists() or target_dir.is_symlink():
        if not force_overwrite:
            return {"restored": False, "reason": f"target exists: {target_dir}; pass --force-overwrite to replace"}
        if target_dir.is_dir() and not target_dir.is_symlink():
            _rmtree_nfs_safe(target_dir)
        else:
            target_dir.unlink()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target_dir, symlinks=True)
    digest, items = rel_sha256_and_items(target_dir)
    return {"restored": True, "target": str(target_dir), "sha256": digest, "items": len(items)}


# ─── 第三段：校验铺出 ─────────────────────────────────────────────────────────

def _snapshot_entries(root: Path, root_name: str) -> dict[str, tuple[str, str]]:
    out = {}
    if not root.is_dir() or root.is_symlink():
        return out
    def walk(path: Path, rel: Path):
        for child in sorted(path.iterdir(), key=lambda x: x.name):
            child_rel = rel / child.name
            # NFS creates .nfs* handles asynchronously when an open file is
            # replaced or deleted. They are not snapshot state and may appear
            # between laydown and verification, so both expected and actual
            # trees must ignore them consistently.
            if _is_deploy_noise_name(child.name):
                continue
            if _release_entry_at(root_name, child_rel):
                continue
            if _is_readonly_skill_entry(root_name, child_rel, child):
                continue
            if child.is_symlink():
                out[child_rel.as_posix()] = ("L", os.readlink(child))
            elif child.is_dir():
                out[child_rel.as_posix()] = ("D", "")
                walk(child, child_rel)
            elif child.is_file():
                out[child_rel.as_posix()] = ("F", sha256_file(child))
            else:
                out[child_rel.as_posix()] = ("X", "")
    walk(root, Path())
    return out


def verify_laydown(ws: Path, md_set: set[str], staging_manifest: dict,
                   expected_skills_local: list[str] | None = None,
                   staging: Path | None = None) -> list[str]:
    """Verify deployed md/mcp and the exact non-protected snapshot tree."""
    issues: list[str] = []
    if staging is None:
        raw_staging = staging_manifest.get("_staging_path")
        staging = Path(raw_staging) if raw_staging else None
    for md in md_set:
        if not (ws / md).is_file():
            issues.append(f"md 缺失: {md}")
    if "mcp" in staging_manifest.get("layers", []):
        block = _extract_layer_block(staging_manifest.get("_raw", ""), "mcp")
        if "mcp/mcporter.json" in staging_manifest.get("_raw", "") and "skipped: true" not in block:
            found = next((p for p in (ws / "config" / "mcporter.json", ws / ".mcporter.json") if p.is_file()), None)
            if not found:
                issues.append("mcp: mcporter.json 缺失")
            elif staging is not None:
                expected_mcp = staging / "mcp" / "mcporter.json"
                if expected_mcp.is_file() and sha256_file(found) != sha256_file(expected_mcp):
                    issues.append("mcp: mcporter.json 原始字节不一致")
    if staging is None:
        # Legacy direct callers did not pass staging. Do not invent a runtime
        # validity check; the new contract compares only when package data exists.
        return issues
    try:
        states = _skill_snapshot_root_states(staging, staging_manifest)
    except Exception as exc:
        issues.append(str(exc))
        return issues
    for root_name in ("skills", "skills-local"):
        expected_root = staging / root_name
        actual_root = ws / root_name
        expected = _snapshot_entries(expected_root, root_name) if states[root_name] else {}
        actual = _snapshot_entries(actual_root, root_name) if actual_root.is_dir() and not actual_root.is_symlink() else {}
        if states[root_name] and (actual_root.is_symlink() or not actual_root.is_dir()):
            issues.append(f"{root_name} root 缺失")
        if not states[root_name] and (actual_root.exists() or actual_root.is_symlink()) and not _root_has_protected_entries(actual_root, root_name):
            issues.append(f"{root_name} absent root 未删除")
        for key, value in expected.items():
            if actual.get(key) != value:
                issues.append(f"{root_name}/{key} 不一致")
        for key in set(actual) - set(expected):
            if (root_name == "skills" and key == "skills-local" and
                    _root_has_protected_entries(actual_root, root_name)):
                continue
            if not _release_entry_at(root_name, Path(key)):
                issues.append(f"{root_name}/{key} 多余")
    return issues


def _extract_layer_block(raw_text: str, layer_name: str) -> str:
    """从 manifest 原文里抠出指定 layer 的块（`  - name: <x>` 到下一个 `  - `）。"""
    m = re.search(rf"^  - name:\s*{re.escape(layer_name)}\s*$([\s\S]*?)(?=^  - name:|^excludes:|^$)",
                  raw_text, re.M)
    return m.group(1) if m else ""


def backup_evolve_results(target_dir: Path, backup_dir: Path) -> Path | None:
    """Backup an existing evolve_results/<run-id> directory before replacement."""
    if not (target_dir.exists() or target_dir.is_symlink()):
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", target_dir.name) or "evolve-run"
    bak_tgz = backup_dir / f"{ts}.evolve_results.{safe_name}.bak.tgz"
    skipped: list[str] = []
    with tarfile.open(bak_tgz, "w:gz") as tar:
        skipped.extend(_tar_add_live_tree(tar, target_dir, target_dir.name))
    if skipped:
        sidecar = bak_tgz.with_suffix(bak_tgz.suffix + ".skipped.json")
        try:
            sidecar.write_text(json.dumps({"skipped": skipped[:200], "total": len(skipped)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
    return bak_tgz


def rollback_evolve_results(target_dir: Path | None, bak_tgz: Path | None) -> None:
    """Restore or remove evolve_results target after failed deploy."""
    if not target_dir:
        return
    try:
        if target_dir.exists() or target_dir.is_symlink():
            if target_dir.is_dir() and not target_dir.is_symlink():
                _rmtree_nfs_safe(target_dir)
            else:
                target_dir.unlink()
        if bak_tgz and bak_tgz.is_file():
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(bak_tgz, "r:gz") as tar:
                _safe_extractall(tar, target_dir.parent)
    except Exception as e:
        print(f"  ⚠ evolve_results 回滚失败: {e}", file=sys.stderr)


# ─── 回滚 ────────────────────────────────────────────────────────────────────

def rollback(ws: Path, bak_tgz: Path | None, md_set: set[str]) -> None:
    """从备份恢复。失败时尽力清掉半成品。"""
    if not bak_tgz or not bak_tgz.is_file():
        # 无备份 = 部署前就空，回滚 = 清掉刚铺的
        try:
            _clear_skills_entries(ws / "skills")
        except Exception:
            pass
        _clear_md(ws, md_set)
        _clear_mcp(ws)
        return
    # 先清当前（可能半铺）
    try:
        _clear_skills_entries(ws / "skills")
    except Exception:
        pass
    _clear_md(ws, md_set)
    _clear_mcp(ws)
    # 再从备份恢复
    with tarfile.open(bak_tgz, "r:gz") as tar:
        _safe_extractall(tar, ws)


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="deploy.sh", description="agent 镜像部署（md/mcp/skill，基线回归复现）")
    ap.add_argument("--image", default=None, help="clawevolve-pack 产出的镜像路径（.zip 默认 / .tgz / .tar.gz）")
    ap.add_argument("--image-url", default=None, help="可选：clawweb/OSS 下载 URL；下载到临时文件后部署")
    ap.add_argument("--workspace", default=None, help="目标 workspace（默认探测 ~/.openclaw/workspace）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不写真实 workspace：解包到临时目录后跑校验，验证镜像可复现性")
    ap.add_argument("--no-backup", action="store_true", help="跳过备份（已知干净环境）")
    ap.add_argument("--force-overwrite", action="store_true", help="目标已存在时直接覆盖不报错")
    ap.add_argument("--skip-resolve-auth", action="store_true",
                    help="兼容旧命令保留；MCP 采用原始字节恢复，不再执行 auth_ref 替换")
    ap.add_argument("--skip-evolve-results", action="store_true", default=True,
                    help="跳过 package/clawevolve_results/ 还原（默认行为：只部署 skill/md/mcp 三层）")
    ap.add_argument("--with-evolve-results", action="store_true", default=False,
                    help="显式启用 evolve_results 还原；覆盖默认的跳过行为")
    ap.add_argument("--evolve-results-dir", default=None,
                    help="自进化 run 目录；优先级最高，例如 /home/admin/.openclaw/workspace/clawevolve_results/<evolve-run-id>")
    ap.add_argument("--evolve-run-id", default=None,
                    help=f"自进化 run id；会解析为 {DEFAULT_EVOLVE_RESULTS_BASE}/<evolve-run-id>")
    ap.add_argument("--md-set", default=None,
                    help="自定义 MD 集合（逗号分隔；默认 AGENTS/BOOTSTRAP/IDENTITY/SOUL/TOOLS/USER/RULES/OKR）")
    for flag in RESERVED_WITH_FLAGS:
        ap.add_argument(f"--{flag}", action="store_true", help=RESERVED_WITH_FLAGS[flag])
    args = ap.parse_args(argv)

    for flag in RESERVED_WITH_FLAGS:
        if getattr(args, flag.replace("-", "_")):
            sys.exit(f"clawevolve-deploy: --{flag} 当前未支持（待 patch）。{RESERVED_WITH_FLAGS[flag]}")

    md_set = set(MD_SET_FALLBACK)  # 兜底，被 manifest 实际 names 覆盖
    if args.md_set:
        md_set = {x.strip() for x in args.md_set.split(",") if x.strip()}

    if not args.image and not args.image_url:
        sys.exit("clawevolve-deploy: 需要 --image 或 --image-url")
    temp_download_dir = None
    if args.image_url:
        temp_download_dir = Path(tempfile.mkdtemp(prefix="deploy-image-"))
        try:
            image_path = download_image(args.image_url, temp_download_dir).resolve()
        except Exception as e:
            sys.exit(f"clawevolve-deploy: 镜像下载失败: {e}")
    else:
        image_path = Path(args.image).expanduser().resolve()

    # ── 第一段：校验镜像 ──
    print("=" * 60, file=sys.stderr)
    print("clawevolve-deploy: 第一段 — 校验镜像", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    try:
        staging, manifest = validate_image(image_path)
    except Exception as e:
        sys.exit(f"clawevolve-deploy: 镜像校验失败: {e}")
    print(f"  image: {image_path}", file=sys.stderr)
    print(f"  id: {manifest.get('id')}  version: {manifest.get('version')}", file=sys.stderr)
    print(f"  layers: {manifest.get('layers')}", file=sys.stderr)
    # 用 manifest 实际 md names 覆盖默认 md_set（与 pack 产出精确对齐）
    if manifest.get("md_names"):
        md_set = set(manifest["md_names"])
    snapshot_states = _skill_snapshot_root_states(staging, manifest)
    print(f"  Skill snapshot roots: {snapshot_states}", file=sys.stderr)
    evo_meta = manifest.get("clawevolve_results") or {}
    has_evolve_layer = ("clawevolve_results" in manifest.get("layers", []) and
                        not evo_meta.get("skipped") and
                        (staging / "clawevolve_results").is_dir())
    # 默认只部署 skill/md/mcp 三层；evolve_results 需显式 --with-evolve-results 才还原
    _deploy_evolve = args.with_evolve_results and not args.skip_evolve_results
    evolve_target_dir = resolve_evolve_results_dir(args, manifest) if _deploy_evolve else None
    if has_evolve_layer:
        print(f"  evolve_results layer: yes -> {evolve_target_dir or 'unbound'}", file=sys.stderr)
    else:
        print("  evolve_results layer: none/skipped", file=sys.stderr)

    # ── dry-run：用临时 workspace 跑铺入+校验，不动真实环境 ──
    if args.dry_run:
        print("\n" + "=" * 60, file=sys.stderr)
        print("clawevolve-deploy: dry-run — 临时 workspace 复现 + 校验", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        with tempfile.TemporaryDirectory(prefix="deploy-dryrun-") as tmp:
            fake_ws = Path(tmp) / "workspace"
            fake_ws.mkdir(parents=True, exist_ok=True)
            # dry-run 忠实恢复软链字符串，不访问或校验其目标。
            try:
                laid = laydown(staging, fake_ws, md_set, force_overwrite=False, manifest=manifest)
            except Exception as e:
                sys.exit(f"clawevolve-deploy: dry-run 铺入失败: {e}")
            print(f"  铺入 md: {laid['md']}", file=sys.stderr)
            print(f"  铺入 mcp: {laid['mcp']}", file=sys.stderr)
            print(f"  铺入 skills 顶层: {laid['skills_top']}", file=sys.stderr)
            print(f"  铺入 skills-local: {laid['skills_local']}", file=sys.stderr)
            # 校验归档 entry 的类型、内容和原始软链 target；断链本身是合法快照状态。
            issues = verify_laydown(fake_ws, md_set, manifest, staging=staging)
            if issues:
                print("\n  ❌ 校验失败:", file=sys.stderr)
                for s in issues:
                    print(f"    {s}", file=sys.stderr)
                return 1
            if has_evolve_layer:
                print(f"  ℹ️ dry-run 未写真实 evolve_results；目标将是: {evolve_target_dir or '未绑定'}", file=sys.stderr)
            print("\n  ✅ dry-run 校验通过（双 Skill root/md/mcp 结构完整）", file=sys.stderr)
        return 0

    # ── 真实部署 ──
    ws = detect_workspace(args.workspace)
    if not ws or not ws.is_dir():
        sys.exit("clawevolve-deploy: 找不到目标 workspace（用 --workspace 指定，或设 $WORKSPACE/$OPENCLAW_HOME）。")
    ws = ws.resolve()
    print(f"\n  target workspace: {ws}", file=sys.stderr)

    # 备份
    bak_tgz = None
    evo_bak_tgz = None
    if not args.no_backup:
        print("\n" + "=" * 60, file=sys.stderr)
        print("clawevolve-deploy: 第二段前 — 备份", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        bak_dir = ws / ".deploy-backups"
        try:
            bak_tgz = backup_workspace(ws, md_set, bak_dir)
            print(f"  备份: {bak_tgz}", file=sys.stderr) if bak_tgz else print("  无需备份（workspace 原本为空）", file=sys.stderr)
            if has_evolve_layer and _deploy_evolve and evolve_target_dir:
                evo_bak_tgz = backup_evolve_results(evolve_target_dir, bak_dir)
                if evo_bak_tgz:
                    print(f"  evolve_results 备份: {evo_bak_tgz}", file=sys.stderr)
        except Exception as e:
            sys.exit(f"clawevolve-deploy: 备份失败（中止，未改动 workspace）: {e}")

    # 第二段：清 + 铺
    print("\n" + "=" * 60, file=sys.stderr)
    print("clawevolve-deploy: 第二段 — 清空 + 铺入", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    try:
        # Skill roots are cleared inside _apply_skill_snapshot after no-follow
        # parent preflight; clearing them here would hide an unsafe existing
        # symlink parent when --force-overwrite is false.
        removed_md = _clear_md(ws, md_set)
        removed_mcp = _clear_mcp(ws)
        print("  清空 skills entries: deferred to snapshot restore", file=sys.stderr)
        print(f"  清空 md: {removed_md}", file=sys.stderr)
        print(f"  清空 mcp: {removed_mcp}", file=sys.stderr)
        laid = laydown(staging, ws, md_set, force_overwrite=args.force_overwrite, manifest=manifest)
        print(f"  铺入 md: {laid['md']}", file=sys.stderr)
        print(f"  铺入 mcp: {laid['mcp']}", file=sys.stderr)
        print(f"  铺入 skills 顶层: {laid['skills_top']}", file=sys.stderr)
        print(f"  铺入 skills-local: {laid['skills_local']}", file=sys.stderr)
        if laid["skipped_obsolete_skill_links"]:
            print(f"  跳过旧 Pack 中已删除的 Skill 软链: {laid['skipped_obsolete_skill_links']}", file=sys.stderr)
        if has_evolve_layer and _deploy_evolve:
            if not evolve_target_dir:
                raise RuntimeError("镜像含 evolve_results，但无法解析目标 run_dir；请传 --evolve-results-dir/--evolve-run-id 或设置 EVOLVE_RUN_DIR")
            evo_laid = laydown_evolve_results(staging, evolve_target_dir, force_overwrite=args.force_overwrite)
            if not evo_laid.get("restored"):
                raise RuntimeError(f"evolve_results 还原失败: {evo_laid.get('reason')}")
            laid["clawevolve_results"] = evo_laid
            print(f"  铺入 evolve_results: {evo_laid['target']} ({evo_laid['items']} items)", file=sys.stderr)
    except Exception as e:
        print(f"\n  ❌ 铺入失败: {e} —— 回滚", file=sys.stderr)
        rollback(ws, bak_tgz, md_set)
        rollback_evolve_results(evolve_target_dir, evo_bak_tgz)
        return 2

    # 第三段：校验
    print("\n" + "=" * 60, file=sys.stderr)
    print("clawevolve-deploy: 第三段 — 校验铺出", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    issues = verify_laydown(ws, md_set, manifest, staging=staging)
    if issues:
        print(f"  ❌ 校验失败 ({len(issues)} 项):", file=sys.stderr)
        for s in issues:
            print(f"    {s}", file=sys.stderr)
        print("\n  → 自动回滚", file=sys.stderr)
        rollback(ws, bak_tgz, md_set)
        rollback_evolve_results(evolve_target_dir, evo_bak_tgz)
        return 3
    print("  ✅ 校验通过", file=sys.stderr)
    evo_status = "有" if laid.get("clawevolve_results") else "无"
    print(f"  md({len(laid['md'])}) / mcp({'有' if laid['mcp'] else '无'}) / "
          f"skills_top({len(laid['skills_top'])}) / skills_local({len(laid['skills_local'])}) / "
          f"evolve_results({evo_status})", file=sys.stderr)
    if bak_tgz:
        print(f"  备份: {bak_tgz}（如需回滚：tar xzf <bak> -C {ws}）", file=sys.stderr)
    print("\n  部署完成。bot 重启后激活语义恢复。", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
