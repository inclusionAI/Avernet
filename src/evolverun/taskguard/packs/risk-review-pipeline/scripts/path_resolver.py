#!/usr/bin/env python3
"""
统一路径检测工具 - 适配本地开发环境和线上沙箱环境

两种环境的目录结构:

本地环境:
  项目根目录/
  ├── .claude/skills/{skill-name}/scripts/  (技能脚本)
  └── .openclaw/workspace/data/  (数据目录 - 必须在项目内)

线上沙箱:
  /home/admin/.openclaw/workspace/
  ├── skills/{skill-name}/scripts/  (技能脚本)
  └── data/  (数据目录)

用法:
    from path_resolver import resolve_paths
    paths = resolve_paths(__file__)

    # 获取各种路径
    paths['running_data']     # RUNNING_DATA 目录
    paths['important_file']   # IMPORTANT_FILE 目录 (marketing_flow_config.py所在)
    paths['skill_dir']        # 当前skill根目录
    paths['skills_dir']       # 所有skills的父目录
    paths['references_dir']   # 当前skill的references目录
    paths['workspace_data']   # workspace/data 目录

重要: 禁止回退到 ~/.openclaw/workspace/data，避免数据分散

路径分裂问题:
  本地开发时可能同时存在两个数据目录：
  1. ~/.openclaw/workspace/data/ (HOME路径 - 历史残留或错误用法)
  2. {项目根}/.openclaw/workspace/data/ (项目路径 - 正确)

  本模块始终优先使用项目路径，当检测到HOME路径也有数据时会发出警告。
  线上沙箱环境不存在此问题，只有一条路径。
"""

import os
import sys
import shutil
import glob


# 线上沙箱环境的固定路径
SANDBOX_WORKSPACE = "/home/admin/.openclaw/workspace"
SANDBOX_SKILLS_DIR = "/home/admin/.openclaw/workspace/skills"
SANDBOX_SKILLS_LOCAL_DIR = "/home/admin/.openclaw/workspace/skills-local"
SANDBOX_DATA_DIR = "/home/admin/.openclaw/workspace/data"

# 线上生产环境（openclawExt）的固定路径
OPENCLAWEXT_PACKS_DIR = "/home/admin/openclawExt/clawmind/packs"
OPENCLAWEXT_WORKSPACE = "/home/admin/openclawExt/clawmind"


def _find_project_root(start_path: str) -> str:
    """
    从起始路径向上查找项目根目录或沙箱 workspace 根目录。

    判定优先级：
      1. 本地开发: 目录下同时存在 .claue 和 .openclaw
      2. 线上沙箱: 目录为 /home/admin/.openclaw/workspace（含 skills/ 和 data/）
      3. 沙箱 workspace 的父级: /home/admin/.openclaw

    Args:
        start_path: 起始路径

    Returns:
        str: 项目根目录或沙箱 workspace，如果找不到返回 None
    """
    current = start_path
    while current and current != '/':
        # 本地开发: .claude + .openclaw
        claude_dir = os.path.join(current, '.claude')
        openclaw_dir = os.path.join(current, '.openclaw')
        if os.path.isdir(claude_dir) and os.path.isdir(openclaw_dir):
            return current

        # 线上沙箱 workspace: /home/admin/.openclaw/workspace
        if (current == "/home/admin/.openclaw/workspace" and
            os.path.isdir(os.path.join(current, 'skills')) and
            os.path.isdir(os.path.join(current, 'data'))):
            return current

        # 沙箱环境父级兜底
        if current == "/home/admin/.openclaw":
            ws = os.path.join(current, 'workspace')
            if os.path.isdir(os.path.join(ws, 'skills')) and os.path.isdir(os.path.join(ws, 'data')):
                return ws

        # 线上生产环境 (openclawExt): /home/admin/openclawExt/clawmind
        if current == OPENCLAWEXT_WORKSPACE:
            packs_dir = os.path.join(current, 'packs')
            if os.path.isdir(packs_dir):
                return current

        current = os.path.dirname(current)
    return None


def resolve_paths(caller_file: str) -> dict:
    """
    根据调用脚本的 __file__ 自动检测环境，返回所有关键路径。

    Args:
        caller_file: 调用方的 __file__ 值

    Returns:
        dict: 包含所有关键路径的字典
    """
    # 如果 caller_file 不可靠（如 heredoc 模式下的 <stdin>），回退到本模块路径
    if not caller_file or caller_file in ('<stdin>', '<string>', '-c', os.devnull):
        caller_file = __file__
    script_dir = os.path.dirname(os.path.abspath(caller_file))
    skill_dir = os.path.dirname(script_dir)
    skills_dir = os.path.dirname(skill_dir)

    # Detect workflow-pack layout: scripts/ contains sibling skill packages
    # If data_preprocessing/ exists in script_dir, we're in a workflow pack
    _workflow_pack_layout = os.path.isdir(os.path.join(script_dir, 'data_preprocessing'))

    if _workflow_pack_layout:
        # Workflow pack layout: skill_dir = pack root, skills_dir = script_dir (for imports)
        # references_dir stays at pack_root/references
        skills_dir = script_dir  # skill packages are in scripts/ for imports

    # 检测是否在标准沙箱环境中
    # 判断依据：skills_dir 是否为沙箱的 skills/ 或 skills-local/ 目录
    is_sandbox = (skills_dir == SANDBOX_SKILLS_DIR or skills_dir == SANDBOX_SKILLS_LOCAL_DIR)

    # 或者检测脚本路径是否包含沙箱前缀
    if not is_sandbox:
        normalized_path = os.path.normpath(script_dir)
        is_sandbox = (
            normalized_path.startswith(SANDBOX_SKILLS_DIR) or
            normalized_path.startswith(SANDBOX_SKILLS_LOCAL_DIR)
        )

    # 检测是否在线上生产环境 (openclawExt packs 目录)
    is_openclawext = os.path.normpath(script_dir).startswith(OPENCLAWEXT_PACKS_DIR)

    # 数据目录检测
    # 优先级:
    #   1. 线上沙箱环境: /home/admin/.openclaw/workspace/data
    #   2. 线上生产环境 (openclawExt): /home/admin/openclawExt/clawmind/data
    #   3. 项目内的 .openclaw/workspace/data（本地开发）
    #   4. 错误：禁止回退到 ~/.openclaw/workspace/data
    workspace_data = None

    # 先检查是否在沙箱环境
    if is_sandbox:
        workspace_data = SANDBOX_DATA_DIR
    elif is_openclawext:
        workspace_data = os.path.join(OPENCLAWEXT_WORKSPACE, 'data')
    else:
        # 从 skills_dir 向上查找项目根目录
        project_root = _find_project_root(skills_dir)

        if project_root:
            # 项目内的 .openclaw/workspace/data
            workspace_data = os.path.join(project_root, '.openclaw', 'workspace', 'data')
            if not os.path.exists(workspace_data):
                # 创建目录
                os.makedirs(workspace_data, exist_ok=True)
        else:
            # 错误：无法找到项目根目录
            raise RuntimeError(
                f"无法找到项目根目录（包含 .claude 目录）。请确保在正确的项目目录下运行脚本。\n"
                f"当前 skills_dir: {skills_dir}\n"
                f"如果这是本地开发，请确保项目根目录下存在 .claude 目录。"
            )

    running_data = os.path.join(workspace_data, "RUNNING_DATA")
    important_file = os.path.join(workspace_data, "IMPORTANT_FILE")

    # 确保目录存在
    os.makedirs(running_data, exist_ok=True)

    # Ensure script_dir is in sys.path for workflow-pack layout (skill packages live alongside scripts)
    if _workflow_pack_layout and script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    return {
        'script_dir': script_dir,
        'skill_dir': skill_dir,
        'skills_dir': skills_dir,
        'running_data': running_data,
        'important_file': important_file,
        'workspace_data': workspace_data,
        'references_dir': os.path.join(skill_dir, 'references'),
        'is_sandbox': is_sandbox,
    }


def setup_config_import(caller_file: str):
    """
    [已弃用] 设置配置导入路径。

    此函数已不再需要，因为所有脚本已将必要的函数内联。
    保留此函数仅为向后兼容。

    Args:
        caller_file: 调用方的 __file__ 值
    """
    paths = resolve_paths(caller_file)
    important_file = paths['important_file']

    if os.path.exists(important_file) and important_file not in sys.path:
        sys.path.insert(0, important_file)

    # 兼容: 也检查 workspace/data 目录 (部分模块可能直接放在data下)
    workspace_data = paths['workspace_data']
    if os.path.exists(workspace_data) and workspace_data not in sys.path:
        sys.path.insert(0, workspace_data)


def get_other_skill_dir(caller_file: str, skill_name: str) -> str:
    """
    获取其他skill的目录路径。

    优先在 skills-local/ 下查找，再在 skills/ 下查找。

    Args:
        caller_file: 调用方的 __file__ 值
        skill_name: 目标skill名称

    Returns:
        str: 目标skill的绝对路径
    """
    paths = resolve_paths(caller_file)

    # 如果在沙箱环境，优先查找 skills-local/，再查找 skills/
    if paths.get('is_sandbox'):
        for skills_dir in [SANDBOX_SKILLS_LOCAL_DIR, SANDBOX_SKILLS_DIR]:
            candidate = os.path.join(skills_dir, skill_name)
            if os.path.isdir(candidate):
                return candidate
        # 都不存在时返回默认路径（skills-local 优先）
        return os.path.join(SANDBOX_SKILLS_LOCAL_DIR, skill_name)

    # 其他环境使用检测到的skills目录
    return os.path.join(paths['skills_dir'], skill_name)


def _get_home_data_dir() -> str:
    """获取HOME路径下的数据目录（仅用于路径分裂检测）"""
    return os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", "data")


def _detect_and_migrate_path_split(project_data_dir: str) -> None:
    """
    检测并处理HOME路径与项目路径的分裂问题

    当本地开发时可能同时存在两个数据目录：
    - ~/.openclaw/workspace/data/RUNNING_DATA/ (HOME路径)
    - {项目根}/.openclaw/workspace/data/RUNNING_DATA/ (项目路径)

    此函数检测HOME路径下是否有RUNNING_DATA中的营销活动文件，
    如果有则迁移到项目路径并清理HOME路径，避免数据分散。

    Args:
        project_data_dir: 项目内的数据目录路径
    """
    home_data_dir = _get_home_data_dir()
    home_running_data = os.path.join(home_data_dir, "RUNNING_DATA")
    project_running_data = os.path.join(project_data_dir, "RUNNING_DATA")

    # 检查HOME路径下是否有营销活动文件
    if not os.path.exists(home_running_data):
        return

    # 查找HOME路径下的营销活动文件
    pattern = os.path.join(home_running_data, "营销活动_*.txt")
    home_files = glob.glob(pattern)

    if not home_files:
        return

    print(f"[WARNING] 检测到路径分裂！HOME路径 {home_running_data} 下有 {len(home_files)} 个营销活动文件", file=sys.stderr)
    print(f"[WARNING] 正确的数据目录应为: {project_running_data}", file=sys.stderr)

    # 迁移文件：将HOME路径的文件移动到项目路径
    os.makedirs(project_running_data, exist_ok=True)
    migrated = 0
    for home_file in home_files:
        filename = os.path.basename(home_file)
        project_file = os.path.join(project_running_data, filename)

        # 仅当项目路径中不存在同名文件时才迁移
        if not os.path.exists(project_file):
            try:
                shutil.move(home_file, project_file)
                migrated += 1
            except Exception as e:
                print(f"[WARNING] 迁移文件失败 {filename}: {e}", file=sys.stderr)

    if migrated > 0:
        print(f"[INFO] 已将 {migrated} 个文件从HOME路径迁移到项目路径", file=sys.stderr)
    else:
        print(f"[INFO] HOME路径下的文件已在项目路径中存在，无需迁移", file=sys.stderr)

    # 清理HOME路径下的空RUNNING_DATA目录
    remaining = os.listdir(home_running_data)
    if not remaining or all(f.startswith('.') for f in remaining):
        try:
            shutil.rmtree(home_running_data, ignore_errors=True)
            print(f"[INFO] 已清理空的HOME路径 RUNNING_DATA 目录", file=sys.stderr)
        except Exception:
            pass


def get_data_dir() -> str:
    """
    获取数据目录路径。

    优先级：
      1. 线上沙箱环境: /home/admin/.openclaw/workspace/data（不存在.claude，靠沙箱目录特征判断）
      2. 项目内 .openclaw/workspace/data（本地开发，通过查找.claude定位项目根）

    注意：本地开发时禁止使用 ~/.openclaw/workspace/data，必须使用项目内路径。
    当检测到HOME路径和项目路径同时存在时，自动迁移HOME路径的文件。

    Returns:
        str: 数据目录的绝对路径

    Raises:
        RuntimeError: 无法找到项目根目录时抛出
    """
    # 优先检查沙箱环境（沙箱没有.claude目录，靠顶层路径判断）
    sandbox_data = SANDBOX_DATA_DIR
    if os.path.exists(sandbox_data):
        return sandbox_data

    # 检查线上生产环境 (openclawExt)
    openclawext_data = os.path.join(OPENCLAWEXT_WORKSPACE, 'data')
    if os.path.exists(openclawext_data):
        return openclawext_data

    # 从当前脚本位置向上查找项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = _find_project_root(script_dir)

    if project_root:
        data_dir = os.path.join(project_root, '.openclaw', 'workspace', 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)

        # 检测路径分裂：如果HOME路径也有数据，迁移到项目路径
        _detect_and_migrate_path_split(data_dir)

        return data_dir

    raise RuntimeError(
        "无法找到项目根目录。请确保在正确的项目目录下运行脚本。\n"
        "如果这是本地开发，请确保项目根目录下存在 .claude 目录。\n"
        "如果是线上沙箱环境，请检查 SANDBOX_DATA_DIR 路径是否正确。"
    )


def get_running_data_dir() -> str:
    """
    获取 RUNNING_DATA 目录路径。

    Returns:
        str: RUNNING_DATA 目录的绝对路径
    """
    running_data = os.path.join(get_data_dir(), "RUNNING_DATA")
    os.makedirs(running_data, exist_ok=True)
    return running_data


def get_important_file_dir() -> str:
    """
    获取 IMPORTANT_FILE 目录路径。

    Returns:
        str: IMPORTANT_FILE 目录的绝对路径
    """
    return os.path.join(get_data_dir(), "IMPORTANT_FILE")