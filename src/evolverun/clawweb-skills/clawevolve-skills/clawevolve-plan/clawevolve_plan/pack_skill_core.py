#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pack.py — agent 镜像打包核心（pack-skill）。

把当前 bot 的 agent 配置物料（md / mcp / skill）打成不可变自包含 .zip 镜像，
用于基线回归。范围与规则见 ../SKILL.md。

设计要点：
  - handler 注册表驱动（PACK_HANDLERS）；新增打包项 = 加 handler + 加 --with-<layer>
    开关 + 一条 manifest layer，不改既有逻辑（patch 洞）。
  - skill 层顶层软链保留（指向共享挂载/私货），skills-local 实体拷入。
  - mcp 凭证脱敏为 auth_ref（零明文）；末尾零明文扫描断言。
  - manifest 为开放数组 layers，schema 向后兼容。
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

SCHEMA_VERSION = 1
MIN_OPENCLAW_VERSION = "2026.5.22"
DEFAULT_EVOLVE_RESULTS_BASE = "/home/admin/.openclaw/workspace/clawevolve_results"

# 顶层 md:persona 配置白名单(与 SKILL.md L24 对齐:保留 persona 配置,排除运行时
# 文档如 fix_record / SPEC / clawbench_review)。MEMORY/HEARTBEAT 是运行时状态也排除。
# 白名单制让镜像只含 1:1 复现需要的 persona,避免把工作文档当作配置带走。
MD_INCLUDE = {
    "SOUL.md", "AGENTS.md", "TOOLS.md", "IDENTITY.md",
    "RULES.md", "OKR.md", "USER.md", "BOOTSTRAP.md",
}
MD_EXCLUDE = {"MEMORY.md", "HEARTBEAT.md"}  # 保留作 manifest excluded 记录展示
# skills/ 第一层中排除的项 / 活跃集元数据(后者归 --with-activeset)
#   - skills-repo / skills-center:共享挂载源。新 bot 自带同路径挂载,**不在包内打实体**;
#     顶层软链原样保留指向它,deploy 后软链落在新 bot 同路径即恢复激活。
#   - skills-local:私货物料库。第一阶段顶层迭代 skip;第二阶段**整目录磨实体**拷到
#     package/skills/skills-local/(A 的 skills-local 内本就是实体,磨平不变;
#     若内含子软链则一并磨掉,保证包内私货自包含可重放)。
#   - 活跃集元数据:归 --with-activeset,本次不打。
SKILL_SKIP_TOP = {
    "skills-repo", "skills-center", "skills-local",
    ".current_skill_set", "skill_sets.json", "skill_parameters.json",  # 活跃集元数据(待 patch)
}
RELEASE_MANAGED_SKILL_PREFIXES = ("clawevolve-", "clawbench-", "ocb-")


def _is_release_managed_skill_name(name: str) -> bool:
    value = str(name or "")
    candidate = value[1:] if value.startswith(".") else value
    candidate = candidate.split(".backup.", 1)[0].split(".incoming.", 1)[0]
    return candidate.startswith(RELEASE_MANAGED_SKILL_PREFIXES)
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


def detect_workspace(arg: str | None) -> Path | None:
    if arg:
        return Path(arg).expanduser()
    for cand in (
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
    # 优先级：--bot-id > 代码默认值。禁止从环境变量隐式推断 bot id。
    if bot_id:
        id_ = bot_id.strip()
    else:
        host = os.uname().nodename
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


# ─── mcp 凭证脱敏 → auth_ref(零明文)────────────────────────────────────────

# 密钥字段名识别(脱敏 + 扫描兜底共用)。含 bare token/apikey/passwd:
# MCP env/header 里任何 token*/apikey*/password* 命名都按密钥处理(零明文优先,宁可多脱敏)。
_SECRET_WORDS = (r"api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|"
                 r"authorization|x-ling-auth|client[_-]?secret|password|passwd|credential|bearer")
SECRET_KEY_RE = re.compile(r"(?i)(" + _SECRET_WORDS + r")")
# URL 内嵌凭证:scheme://user:pass@host 或 ?/& token= / access_token= / api_key=
URL_CRED_RE = re.compile(
    r"(?i)(://[^/\s:@]+:[^/\s:@]+@|[?&](?:access_)?token=|[?&]api_?key=)"
)
# 占位符判定:仅放行明显模板占位符;故意不含 example/placeholder 等易误判词
# (避免真实 URL 域名 faas.example.com 里的 example 被当占位符、导致该字段漏脱敏)
_PLACEHOLDER_PAT = r"(?i)(your[_-]?(?:key|token|secret)|<[^>]+>|\bchangeme\b|\bx{3,}\b)"
PLACEHOLDER_RE = re.compile(_PLACEHOLDER_PAT)


def _redact_value(value, server_code: str, field_path: str, fields_redacted: list[str]):
    """若字符串疑似明文密钥/含 URL 凭证 → 替换为 auth_ref 占位对象;否则原样返回。"""
    if not isinstance(value, str):
        return value
    if PLACEHOLDER_RE.search(value):
        return value  # 明显是占位符,保留
    hit_secret = bool(SECRET_KEY_RE.search(field_path)) and len(value) >= 6
    hit_url = bool(URL_CRED_RE.search(value))
    if not (hit_secret or hit_url):
        return value
    fields_redacted.append(f"{server_code}:{field_path}")
    return {"auth_ref": {"source": "user_mcp_config", "server_code": server_code, "fields": [field_path]}}


def redact_mcporter(data, fields_redacted: list[str]):
    """递归把 mcporter.json 里的明文密钥/URL 凭证脱敏成 auth_ref。
    兼容 mcpServers{}/servers[] 等常见布局;server_code 取 name/server_code/command 兜底。"""
    if isinstance(data, dict):
        servers = data.get("mcpServers") or data.get("servers")
        if isinstance(servers, dict):
            for sname, sconf in servers.items():
                if isinstance(sconf, dict):
                    _redact_server(sconf, str(sname), fields_redacted)
        elif isinstance(servers, list):
            for sconf in servers:
                if isinstance(sconf, dict):
                    code = str(sconf.get("server_code") or sconf.get("name") or sconf.get("command") or "?")
                    _redact_server(sconf, code, fields_redacted)
        # 兜底:顶层也可能是单 server
        if "server_code" in data or "command" in data:
            code = str(data.get("server_code") or data.get("name") or data.get("command") or "?")
            _redact_server(data, code, fields_redacted)
    return data


def _redact_server(sconf: dict, code: str, fields_redacted: list[str]):
    # headers / env 里的密钥项
    for key in ("headers", "env"):
        bucket = sconf.get(key)
        if isinstance(bucket, dict):
            for k, v in list(bucket.items()):
                bucket[k] = _redact_value(v, code, f"{key}.{k}", fields_redacted)
    # endpoint/url/baseUrl 内嵌凭证
    for key in ("endpoint", "url", "baseUrl", "server_url"):
        if isinstance(sconf.get(key), str):
            sconf[key] = _redact_value(sconf[key], code, key, fields_redacted)
    # 形如 api_key: "NAME=VALUE" 或 顶层鉴权键
    for k, v in list(sconf.items()):
        kl = k.lower()
        if isinstance(v, str) and SECRET_KEY_RE.search(kl):
            sconf[k] = _redact_value(v, code, k, fields_redacted)


# ─── 零明文扫描断言 ───────────────────────────────────────────────────────────
# 设计立场:**宁可错抓,不可漏放**。这是镜像入 OSS/共享前的硬安全闸门,凭证硬规则要求
# 镜像零明文。故 SCAN_RE 保持宽匹配(带引号值 **和** 裸值都抓),误报靠**保守的放行判定**
# 消除--只有"明显不是密钥"才放行,任何不确定都保留命中。
# (故意不采纳 SPEC-pack-skill-fix #1 的"只匹配带引号值"方案:那会漏掉裸值
#   api_key=AKIA... 这类最常见的真实泄漏,直接击穿零明文断言。误报治理在"放行判定",
#   不在"收窄匹配"。同理不把 URL token 阈值提到 20 字符--会漏短真 token;demo 值改由
#   放行判定里的 demo/sample/... 词剔除。)

SCAN_RE = re.compile(
    r"(?i)("
    r"api[_-]?key\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+]{8,}"
    r"|(?:access[_-]?)?token\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+]{8,}"
    r"|authorization\s*[:=]\s*[\"']?(?:bearer\s+)?[A-Za-z0-9_\-./+]{8,}"
    r"|x-ling-auth\s*[:=]\s*[\"']?[A-Za-z0-9_\-./+]{8,}"
    r"|[\"'][^\"']*\b(?:" + _SECRET_WORDS + r")\b[^\"']*[\"']\s*:\s*[\"'][^\"']{8,}[\"']"
    r"|://[^/\s:@]+:[^/\s:@]+@[^\s/]+"
    r"|[?&](?:access_)?token=[A-Za-z0-9_\-./+%]{8,}"
    r"|[?&]api_?key=[A-Za-z0-9_\-./+%]{8,}"
    r")"
)

# 明显非密钥信号(仅扫描放行用;不影响脱敏路径的 PLACEHOLDER_RE)。demo/sample/your-key 等
# 占位与样例词。注意:**不**泛放行 <...> 角括号内容(api_key=<realkey> 仍需抓)。
_NONSECRET_WORDS_RE = re.compile(
    r"(?i)(demo|sample|dummy|placeholder|changeme|replaceme|todo|"
    r"your[_\-]?(?:key|token|secret)|\bxxxx\b)"
)
# 环境变量引用形:$VAR / ${VAR} -- 非明文
_ENVREF_RE = re.compile(r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?")
# 纯小写变量名（仅小写字母+下划线+点，无数字/大写/连字符）——代码标识符 RHS，非密钥值
_VARNAME_ONLY_RE = re.compile(r"^[a-z][a-z_.]+$")


def _is_obvious_non_secret(seg: str, line: str, m_end: int) -> bool:
    """保守放行判定:命中段"明显不是密钥"才返回 True。任何不确定返回 False(保留命中)。
    消除常见误报:代码里的变量间赋值(api_key=llm_api_key)、类型注解(token: Optional[str])、
    env 引用(Bearer ${VAR})、文档 demo 值(?token=c8b4c4demo)。真凭证形(带数字/大写/连字符/
    高熵) 不会命中本判定而被保留。"""
    if _NONSECRET_WORDS_RE.search(seg):           # demo/your-key/changeme/... 占位与样例词
        return True
    if _ENVREF_RE.search(seg):                    # $VAR / ${VAR} 环境变量引用,非明文
        return True
    if m_end < len(line) and line[m_end] == "[":  # 类型注解尾随 '[',如 token: Optional[str]
        return True
    # 取命中段内 = / : 之后的"值",判断是否纯小写变量名(变量间赋值,非密钥值)
    mv = re.search(r"[:=]\s*[\"']?\s*(\S+)$", seg)
    if mv:
        val = mv.group(1).strip().strip("\"'")
        if 2 <= len(val) <= 32 and _VARNAME_ONLY_RE.fullmatch(val):
            return True
    return False


def scan_plaintext_secrets(root: Path, ignore_patterns: list[str]) -> list[str]:
    """扫描打包内容,返回命中 (file:line:match)。明显非密钥(见 _is_obvious_non_secret)
    与用户 --ignore-secret-pattern 放行;auth_ref 槽位是对象不会命中。"""
    ignore = [re.compile(p) for p in ignore_patterns]
    hits = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in SCAN_RE.finditer(line):
                seg = line[m.start():m.end()]
                if _is_obvious_non_secret(seg, line, m.end()):
                    continue
                if any(rx.search(seg) for rx in ignore):
                    continue
                hits.append(f"{p.relative_to(root)}:{i}: {seg.strip()[:120]}")
    return hits


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
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        return {"name": "mcp", "path": "mcp/mcporter.json", "sha256": "",
                "skipped": True, "reason": f"mcporter.json 解析失败: {e}", "source": str(src)}
    fields_redacted = []
    redact_mcporter(data, fields_redacted)
    dst = staging / "mcp"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "mcporter.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest, n = rel_sha256_and_items(dst)
    return {"name": "mcp", "path": "mcp/mcporter.json", "sha256": digest,
            "fields_redacted": fields_redacted, "source": str(src)}


def handler_skill(ws: Path, staging: Path, args) -> dict:
    skills_dir = ws / "skills"
    dst = staging / "skills"
    dst.mkdir(parents=True, exist_ok=True)
    skills = []                # 顶层激活技能名
    skipped = []               # 跳过项 / 失败项
    symlinks_preserved = []    # 顶层保留的软链(name -> target),deploy 原样铺回即激活
    skills_local_items = []    # skills-local 私货清单
    if not skills_dir.is_dir():
        return {"name": "skill", "path": "skills/", "sha256": "", "skills": [],
                "skipped": True, "reason": "无 workspace/skills 目录"}

    # 第一阶段:顶层激活技能。
    #   - 软链:原样保留 readlink 的 target 字符串,在包内重建同名软链(不磨实体)。
    #     target 是绝对路径(A 的工作方式,如 /home/admin/.openclaw/workspace/skills/skills-repo/...),
    #     新 bot 只要 home=/home/admin、自身挂了 skills-repo/skills-local,软链落过去就自然指向新 bot
    #     的共享挂载/私货,激活语义与 A 一致。
    #     **不磨实体** = (1) skills-repo 共享技能不入包(平台维护、新 bot 自带同路径挂载);
    #                   (2) 适配分析机环境(readlink 不要求 target 存在,分析机 skills-repo 空挂载也能跑)。
    #   - 实体目录:直接磨实体拷(私货直接放在顶层的少见情况,如 qa-knowledge-card)。
    #   - SKILL_SKIP_TOP 中的 skills-repo/skills-center/skills-local 第一阶段都 skip(第二阶段单独处理 skills-local)。
    for entry in sorted(skills_dir.iterdir()):
        name = entry.name
        if name in SKILL_SKIP_TOP or name.startswith(".") or _is_release_managed_skill_name(name):
            skipped.append(name)
            continue
        target = dst / name
        try:
            if entry.is_symlink():
                link_target = os.readlink(entry)  # 不要求 target 存在
                os.symlink(link_target, target)
                symlinks_preserved.append(f"{name} -> {link_target}")
                skills.append(name)
            elif entry.is_dir():
                shutil.copytree(entry, target, symlinks=False, dirs_exist_ok=False)
                skills.append(name)
            else:
                skipped.append(f"{name}(非目录/非软链,跳过)")
                continue
        except (FileNotFoundError, OSError, shutil.Error) as e:
            # OSS fuse 挂载竞态处理同前:记 skipped、清半成品、不崩溃。
            skipped.append(f"{name}(oss-race: {type(e).__name__})")
            if target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.exists():
                shutil.rmtree(target, ignore_errors=True)
            continue

    # 第二阶段:skills-local 私货物料库 → 整目录拷到 package/skills/skills-local/。
    # A 的 skills-local 内主要是实体技能(用户私货,如 bot-pack、新版 web_search_asap),
    # 但也可能含运行时产物软链(如 clawbench/tasks/clawbench_results/bench_*/input → workspace
    # 外的 clawbench_results/,这些 target 在分析机上不通、也不该入包)。
    # 故用 symlinks=True:保留软链原样不解析(避免 FileNotFoundError 中断整目录拷贝);
    # 顶层激活集的"磨实体"已在第一阶段对 skills-repo 软链做了让步(保留软链不入实体),
    # 同理 skills-local 内的运行时软链也保留路径不解析。新 bot 上 target 存在则恢复、
    # 不存在也无妨(这些是产物非 skill 物料)。
    # deploy 时铺回新 bot 的 skills-local/,顶层软链若有指向 skills-local/<name> 的
    # (如 A 的 clawbench → skills-local/clawbench)自然指到这份私货,激活语义闭环。
    local_dir = skills_dir / "skills-local"
    if local_dir.is_dir():
        dst_local = dst / "skills-local"
        try:
            release_local_entries = {
                entry.name for entry in local_dir.iterdir()
                if _is_release_managed_skill_name(entry.name)
            }
            skipped.extend(f"skills-local/{name}(Release managed)" for name in sorted(release_local_entries))

            def ignore_release_entries(directory, names):
                if Path(directory).resolve() != local_dir.resolve():
                    return set()
                return set(names) & release_local_entries

            shutil.copytree(
                local_dir,
                dst_local,
                symlinks=True,
                dirs_exist_ok=False,
                ignore=ignore_release_entries,
            )
        except (FileNotFoundError, OSError, shutil.Error) as e:
            # OSS fuse 挂载竞态处理同前:清半成品、记 skipped、不崩溃。
            skipped.append(f"skills-local(oss-race: {type(e).__name__}: {str(e)[:120]})")
            shutil.rmtree(dst_local, ignore_errors=True)
        else:
            for entry in sorted(local_dir.iterdir()):
                if (
                    entry.name.startswith(".")
                    or _is_release_managed_skill_name(entry.name)
                    or not entry.is_dir()
                ):
                    continue
                skills_local_items.append(entry.name)

    # 不变量断言(已调整):**顶层允许软链**(与 A 工作方式一致,指向新 bot 共享挂载/私货),
    # skills-local 内允许运行时产物软链(指向 workspace 外的 benchmark 结果等)。pack 不强制
    # 磨平所有软链,只保证:(1) 顶层软链 target 路径完整保留;(2) skills-local 内实体技能物料完整。
    digest, n = rel_sha256_and_items(dst)
    return {"name": "skill", "path": "skills/", "sha256": digest, "skills": skills,
            "items": n, "excluded": skipped,
            "symlinks_preserved": symlinks_preserved,
            "skills_local_items": skills_local_items}


def resolve_evolve_results_dir(args) -> Path | None:
    """Resolve the evolve run directory without scanning global state.

    Never package the global evolve_results root by default, because the same bot can
    have multiple concurrent evolve runs.
    """
    if getattr(args, "evolve_results_dir", None):
        return Path(args.evolve_results_dir).expanduser()
    if getattr(args, "task_id", None):
        task_id = str(args.task_id).strip().strip("/")
        if task_id:
            return Path(DEFAULT_EVOLVE_RESULTS_BASE) / task_id
    return None

def handler_evolve_results(ws: Path, staging: Path, args) -> dict:
    """Copy the self-evolution run artifacts into the image.

    Source priority:
      --evolve-results-dir
      --task-id -> /home/admin/.openclaw/workspace/clawevolve_results/<task-id>
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
        return {"name": "evolve_results", "path": "clawevolve_results/", "sha256": "",
                "skipped": True, "reason": "no task bound; pass --evolve-results-dir/--task-id or set EVOLVE_RUN_DIR"}
    if not src.exists():
        return {"name": "evolve_results", "path": "clawevolve_results/", "sha256": "",
                "skipped": True, "reason": f"evolve results dir not found: {src}", "source": str(src)}
    if not src.is_dir():
        return {"name": "evolve_results", "path": "clawevolve_results/", "sha256": "",
                "skipped": True, "reason": f"evolve results path is not a directory: {src}", "source": str(src)}

    dst = staging / "clawevolve_results"

    def ignore_noise(_dir, names):
        ignored = set()
        for name in names:
            if name in {".DS_Store", "__pycache__"} or name.endswith(".pyc"):
                ignored.add(name)
        return ignored

    shutil.copytree(src, dst, symlinks=True, ignore=ignore_noise, dirs_exist_ok=False)
    digest, n = rel_sha256_and_items(dst)
    return {"name": "evolve_results", "path": "clawevolve_results/", "sha256": digest,
            "items": n, "source": str(src)}


PACK_HANDLERS = {"md": handler_md, "mcp": handler_mcp, "skill": handler_skill, "evolve_results": handler_evolve_results}


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
        "agent 镜像(基线回归用)。自包含:persona md / MCP(凭证已 auth_ref 脱敏) / skills(实体) / evolve_results(目标、spec、round 产物)。\n\n"
        "## 部署\n"
        "由 deploy-skill(后续步骤)三段式铺设到 eval-carrier bot 沙箱:\n"
        "  md → workspace/*.md;mcp → workspace/config/mcporter.json(凭证由 ocb 按 owner 经 user_mcp_config 还原);\n"
        "  skill → workspace/skills/<name>/ 实体；clawevolve_results → /home/admin/.openclaw/workspace/clawevolve_results/<task-id>。\n\n"
        "## 安全\n"
        f"零明文断言:内容摘要 `{manifest_digest[:16]}...`。含 persona/能力面描述,按敏感文件对待,入 OSS 设访问控制、write-once。\n",
        encoding="utf-8")


# ─── 上传(可选,clawweb 收镜像接口)────────────────────────────────────────

def upload(tar_path: Path, url: str) -> tuple[bool, str]:
    if shutil.which("curl"):
        try:
            r = subprocess.run(
                ["curl", "-sS", "-o", "-", "-w", "\\n%{http_code}", "-X", "POST", url,
                 "-F", f"file=@{tar_path}"],
                capture_output=True, text=True, timeout=600)
            code = r.stdout.strip().splitlines()[-1] if r.stdout else "?"
            return code.startswith("2"), f"curl http={code} {r.stderr.strip()[:200]}"
        except Exception as e:
            return False, f"curl 异常: {e}"
    import urllib.request
    try:
        with tar_path.open("rb") as f:
            req = urllib.request.Request(url, data=f, method="POST",
                headers={"Content-Type": "application/octet-stream"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                return 200 <= resp.status < 300, f"urllib status={resp.status}"
    except Exception as e:
        return False, f"urllib 异常: {e}"


# ─── 主流程 ─────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pack.sh", description="agent 镜像打包(md/mcp/skill,基线回归用)")
    ap.add_argument("--version", default="1.0.0", help="镜像版本(semver,默认 1.0.0)")
    ap.add_argument("--workspace", default=None, help="源 workspace(默认探测 ~/.openclaw/workspace)")
    ap.add_argument("--evolve-results-dir", default=None,
                    help="自进化单次 run 目录；优先级最高，例如 /home/admin/.openclaw/workspace/clawevolve_results/<task-id>")
    ap.add_argument("--task-id", default=None,
                    help=f"任务 ID；会解析为 {DEFAULT_EVOLVE_RESULTS_BASE}/<task-id>")
    ap.add_argument("--bot-id", default=None, help="bot 标识(写入 manifest source.carrierBotId)")
    ap.add_argument("--out-dir", default=".", help="tar 输出目录(默认当前目录)")
    ap.add_argument("--upload-url", default=None, help="可选:打完即 curl 上传 clawweb 收镜像接口")
    ap.add_argument("--ignore-secret-pattern", action="append", default=[], metavar="REGEX",
                    help="零明文扫描放行正则(可多次;用于占位符样例)")
    ap.add_argument("--allow-broken-symlink", action="store_true", help="放行 skill 软链断链(默认报错跳过并记 skipped)")
    # 预留扩展开关:命中即报"未支持/待 patch"
    for flag in RESERVED_WITH_FLAGS:
        ap.add_argument(f"--{flag}", action="store_true", help=RESERVED_WITH_FLAGS[flag])
    args = ap.parse_args(argv)

    for flag in RESERVED_WITH_FLAGS:
        if getattr(args, flag.replace("-", "_")):
            sys.exit(f"pack-skill: --{flag} 当前未支持(待 patch)。{RESERVED_WITH_FLAGS[flag]}")

    ws = detect_workspace(args.workspace)
    if not ws or not ws.is_dir():
        sys.exit("pack-skill: 找不到 workspace(用 --workspace 指定,或设 $WORKSPACE/$OPENCLAW_HOME)。")
    ws = ws.resolve()

    id_slug, title, desc = derive_meta(ws, args.bot_id)

    with tempfile.TemporaryDirectory(prefix="pack-skill-") as tmp:
        staging = Path(tmp) / "package"
        staging.mkdir(parents=True)

        layers = []
        for name in ("md", "mcp", "skill", "evolve_results"):
            try:
                rec = PACK_HANDLERS[name](ws, staging, args)
            except Exception as e:
                sys.exit(f"pack-skill: 物料 {name} 打包失败: {e}")
            layers.append(rec)
            if rec.get("skipped") is True:
                status = "skipped"
            else:
                item_count = rec.get('files', rec.get('items', 0))
                if name == "mcp":
                    status = "ok (mcporter.json)" if item_count else "ok (mcporter.json)"
                else:
                    status = f"ok ({item_count} items)"
            extra = f", redacted={len(rec.get('fields_redacted', []))}" if name == "mcp" else ""
            print(f"  [layer] {name}: {status}{extra}", file=sys.stderr)

        # 零明文扫描断言
        hits = scan_plaintext_secrets(staging, args.ignore_secret_pattern)
        if hits:
            print("pack-skill: 零明文扫描命中疑似明文密钥:", file=sys.stderr)
            for h in hits[:50]:
                print(f"    {h}", file=sys.stderr)
            sys.exit("pack-skill: 拒绝打包(镜像禁止明文密钥)。可用 --ignore-secret-pattern REGEX 放行占位符,或修正源文件。")

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
            "compat": {"schemaVersion": SCHEMA_VERSION, "minOpenclawVersion": MIN_OPENCLAW_VERSION},
            "source": {
                "openclawVersion": _guess_openclaw_version(),
                "host": os.uname().nodename,
                "user": "",
                "carrierBotId": args.bot_id or "",
                "capturedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "contentDigest": content_digest,
                "contentSha8": sha8,
            },
            "secrets": {"plaintext": "none", "scheme": "auth_ref", "resolver": "ocb.user_mcp_config"},
            "layers": layers,
            "excludes": ["skills-repo", "skills-center", "MEMORY.md", "HEARTBEAT.md",
                         ".DS_Store", "__pycache__", "*.pyc", "cache", "logs", "session-cache", "memory"],
            "flags": {"withMemory": False, "withModel": False, "withPlugins": False,
                      "withActiveset": False, "withIdentity": False, "withSessions": False},
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

        print(f"\npack-skill: 已生成镜像", file=sys.stderr)
        print(f"  path:        {zip_path}", file=sys.stderr)
        print(f"  size:        {size} bytes ({size/1024:.1f} KiB)", file=sys.stderr)
        print(f"  sha256:      {zip_sha}", file=sys.stderr)
        print(f"  content:     {content_digest}", file=sys.stderr)
        for rec in layers:
            tag = "SKIP" if rec.get("skipped") is True else rec.get("sha256", "")[:16]
            print(f"  layer {rec['name']:<6}: {tag}", file=sys.stderr)
        mcp_rec = next((r for r in layers if r["name"] == "mcp"), {})
        if mcp_rec.get("fields_redacted"):
            print(f"  mcp redacted fields: {len(mcp_rec['fields_redacted'])} -> auth_ref", file=sys.stderr)
        print(f"  零明文断言: 通过({len(hits)} 命中)", file=sys.stderr)

        if args.upload_url:
            ok, msg = upload(zip_path, args.upload_url)
            print(f"  upload: {'ok' if ok else 'FAILED'} - {msg}", file=sys.stderr)
            if not ok:
                return 3
        return 0


def _write_zip_with_symlinks(staging: Path, zip_path: Path) -> None:
    """用 zipfile 写 zip，手动处理 symlink：
      - file: zf.write(fp, arcname)
      - symlink: 手动构造 ZipInfo，external_attr 高 16 位 = Unix st_mode（含 0o120000 symlink 位），
        body = readlink target 字符串。解包时按 external_attr 重建 symlink。

    os.walk(followlinks=False) 把目录软链放 dirs，文件软链放 files，故两处都要处理 symlink。
    arcname = 相对 staging.parent（保持 `package/` 前缀，与历史 tgz 几何一致）。
    """
    import zipfile
    base = staging.parent  # arcname 前缀 = package/...
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(staging, followlinks=False):
            # 处理目录型 symlink（os.walk 把目录软链放 dirs，需显式 add 然后从 dirs 移除避免跟随）
            for d in list(dirs):
                dp = Path(root) / d
                if dp.is_symlink():
                    arc = str(dp.relative_to(base))
                    _zip_add_symlink(zf, dp, arc)
                    dirs.remove(d)
            for fn in files:
                fp = Path(root) / fn
                arc = str(fp.relative_to(base))
                if fp.is_symlink():
                    _zip_add_symlink(zf, fp, arc)
                else:
                    zf.write(fp, arc)


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
