# PR #491 实施计划

## 目标

将 PR #491 的功能完整复刻到 `REL20260730_zq` 分支，实现 Bot 级别的 rsync exclude 配置功能。

## 前置条件

- [x] 分支已同步到 `REL20260730` 最新代码
- [x] 工作目录干净，无未提交的修改
- [x] 所有目标文件存在且可访问

## 实施步骤

### 第一步：修改基础设施层

#### 1.1 修改 `engines/__init__.py`

**文件路径**: `src/backend/src/agentclaw/community/core/workspace/engines/__init__.py`

**操作**: 添加解析函数

**修改内容**:
```python
# 在文件顶部添加导入
from __future__ import annotations
from typing import Any

# 添加解析函数（在 create_engine_sandbox_registry 之前）
def parse_build_rsync_excludes_from_ext(
    ext: dict[str, Any] | None,
) -> list[str] | None:
    """Parse build_rsync_excludes from ac_bots.ext field.

    Args:
        ext: The parsed JSON dict from ac_bots.ext column.

    Returns:
        None if not configured (use defaults), or a list of exclude patterns.
    """
    if not ext:
        return None

    patterns = ext.get("build_rsync_excludes")
    if not patterns or not isinstance(patterns, list):
        return None

    # Validate all items are strings (convert numbers to strings for safety)
    # Note: bool is a subclass of int, so explicitly exclude bool
    return [
        str(p) for p in patterns
        if isinstance(p, (str, int, float)) and not isinstance(p, bool)
    ]
```

**验证**: `python -c "from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext"`

---

### 第二步：修改接口层

#### 2.1 修改 `engine_sandbox.py`

**文件路径**: `src/backend/src/agentclaw/community/core/workspace/engine_sandbox.py`

**操作**: 修改 `EngineSandboxProvider` 接口的 `get_build_plan` 方法签名

**修改内容**:
```python
# 找到 get_build_plan 方法定义（约 57 行）
def get_build_plan(self) -> EngineBuildPlan:
    ...

# 修改为：
def get_build_plan(
    self,
    build_rsync_excludes_append: list[str] | None = None,
) -> EngineBuildPlan:
    """Return build plan with optional Bot-level build_rsync_excludes append.

    Args:
        build_rsync_excludes_append: Bot-level excludes from ac_bots.ext.
            可选参数，为 None 时使用模块级默认值。
            **合并语义**：与默认值合并（去重），而非完全覆盖。
    """
    ...
```

**验证**: 类型检查通过，无语法错误

---

### 第三步：实现引擎层

#### 3.1 修改 `engines/openclaw.py`

**文件路径**: `src/backend/src/agentclaw/community/core/workspace/engines/openclaw.py`

**操作**: 重构 build plan 创建逻辑

**步骤**:

1. **提取工厂函数**（在 `_OPENCLAW_RSYNC_EXCLUDES` 定义之后）：

```python
# 找到 _OPENCLAW_BUILD_PLAN 定义（约 47-56 行），替换为：

def _make_openclaw_build_plan(rsync_excludes: list[str]) -> EngineBuildPlan:
    """Factory function to create build plan with given excludes."""
    return EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=rsync_excludes,
    )


_OPENCLAW_BUILD_PLAN = _make_openclaw_build_plan(list(_OPENCLAW_RSYNC_EXCLUDES))
```

2. **修改 `get_build_plan` 方法**（在 `OpenClawSandboxProvider` 类中，约 84 行）：

```python
# 找到原来的方法：
def get_build_plan(self) -> EngineBuildPlan:
    return _OPENCLAW_BUILD_PLAN

# 替换为：
def get_build_plan(
    self,
    build_rsync_excludes_append: list[str] | None = None,
) -> EngineBuildPlan:
    # 合并模式：默认值 + 自定义项（去重）
    excludes = list(_OPENCLAW_RSYNC_EXCLUDES)
    if build_rsync_excludes_append:
        # 合并并去重，保持顺序：默认值在前，自定义项追加
        for item in build_rsync_excludes_append:
            if item not in excludes:
                excludes.append(item)
    return _make_openclaw_build_plan(excludes)
```

**验证**: 运行相关测试 `pytest src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py -k openclaw -v`

---

#### 3.2 修改 `engines/claude_code.py`

**文件路径**: `src/backend/src/agentclaw/community/core/workspace/engines/claude_code.py`

**操作**: 同 openclaw.py，重构 build plan 创建逻辑

**步骤**:

1. **提取工厂函数**（在 `_CLAUDE_CODE_RSYNC_EXCLUDES` 定义之后）：

```python
# 找到 _CLAUDE_CODE_BUILD_PLAN 定义（约 86-97 行），替换为：

def _make_claude_code_build_plan(rsync_excludes: list[str]) -> EngineBuildPlan:
    """Factory function to create build plan with given excludes."""
    return EngineBuildPlan(
        engine_type="claude_code",
        source_root_name=".claude_code",
        migration_subpath="claude_code",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        extra_sync_source_relpath=".claude",
        extra_sync_target_relpath="claude",
        rsync_excludes=rsync_excludes,
    )


_CLAUDE_CODE_BUILD_PLAN = _make_claude_code_build_plan(list(_CLAUDE_CODE_RSYNC_EXCLUDES))
```

2. **修改 `get_build_plan` 方法**（在 `ClaudeCodeSandboxProvider` 类中，约 125 行）：

```python
# 找到原来的方法：
def get_build_plan(self) -> EngineBuildPlan:
    return _CLAUDE_CODE_BUILD_PLAN

# 替换为：
def get_build_plan(
    self,
    build_rsync_excludes_append: list[str] | None = None,
) -> EngineBuildPlan:
    # 合并模式：默认值 + 自定义项（去重）
    excludes = list(_CLAUDE_CODE_RSYNC_EXCLUDES)
    if build_rsync_excludes_append:
        # 合并并去重，保持顺序：默认值在前，自定义项追加
        for item in build_rsync_excludes_append:
            if item not in excludes:
                excludes.append(item)
    return _make_claude_code_build_plan(excludes)
```

**验证**: 运行相关测试 `pytest src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py -k claude_code -v`

---

### 第四步：集成到业务层

#### 4.1 修改 `bot_build_service.py`

**文件路径**: `src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py`

**操作**: 在 `build` 方法中读取并传递 rsync excludes 配置

**步骤**:

1. **添加导入**（在文件顶部的 import 区域）：

```python
from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext
```

2. **修改 `build` 方法**（约 219-222 行）：

```python
# 找到原来的代码：
entity_type = bot.get("entity_type", "staff")
device_id = bot.get("device_id")
provider = self._resolve_sandbox_provider(bot)
build_plan = provider.get_build_plan()

# 替换为：
entity_type = bot.get("entity_type", "staff")
device_id = bot.get("device_id")
provider = self._resolve_sandbox_provider(bot)

# 从 bot.ext 解析 build_rsync_excludes 配置
ext = bot.get("ext")
rsync_append = parse_build_rsync_excludes_from_ext(ext)

# 传递 Bot 级别追加项（合并模式）
build_plan = provider.get_build_plan(build_rsync_excludes_append=rsync_append)
```

**验证**: 语法检查无误

---

### 第五步：添加 API 层

#### 5.1 添加响应模型到 `schemas.py`

**文件路径**: `src/backend/src/agentclaw/community/adapters/http/service_bot/schemas.py`

**操作**: 在文件末尾添加新的响应模型

**修改内容**:
```python
# 在文件末尾添加（约 48 行之后）

class BotRsyncExcludesResponse(BaseModel):
    """Bot rsync excludes 配置响应。"""
    bot_id: str = Field(..., description="Bot ID")
    engine_type: str = Field(..., description="引擎类型（openclaw/claude_code）")
    default_excludes: list[str] = Field(..., description="引擎默认的rsync排除规则")
    custom_excludes: Optional[list[str]] = Field(None, description="Bot自定义配置（可能为null）")
    merged_excludes: list[str] = Field(..., description="合并后的最终排除规则（去重）")
    excludes_source: str = Field(..., description="配置来源标识：default_only 或 default_plus_custom")
```

**验证**: 导入成功 `python -c "from agentclaw.community.adapters.http.service_bot.schemas import BotRsyncExcludesResponse"`

---

#### 5.2 添加 API 端点到 `router_build.py`

**文件路径**: `src/backend/src/agentclaw/community/adapters/http/service_bot/router_build.py`

**操作**: 添加新的 GET 端点

**步骤**:

1. **添加导入**（在文件顶部的 import 区域，约 17 行）：

```python
from agentclaw.community.adapters.http.service_bot.schemas import (
    ApiResponse,
    BotBuildRequest,
    BotRsyncExcludesResponse,  # 新增
    ReadOnlyRuleItem,
    ReadOnlyTreeItem,
    ReadOnlyTreeResponse,
)
```

2. **添加新端点**（在文件末尾，约 278 行之后）：

```python
# ---------------------------------------------------------------------------
# Rsync excludes configuration
# ---------------------------------------------------------------------------


@router.get(
    "/rsync-excludes",
    response_model=ApiResponse,
    summary="获取Bot的rsync excludes配置",
)
@with_interceptors(CollaboratorPermissionInterceptor(
    bot_id="$bot_id",
    owner_id="$owner_id",
    persist_audit_log=False,  # 只读操作
))
async def get_bot_rsync_excludes(
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot owner ID"),
    user: AuthenticatedUser = Depends(get_current_user),
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
) -> ApiResponse:
    """获取Bot的rsync excludes配置信息。

    返回三部分信息：
    1. default_excludes: 引擎默认的rsync排除规则
    2. custom_excludes: Bot ext中配置的自定义排除规则
    3. merged_excludes: 合并后的最终排除规则（默认值+自定义项，去重）

    GET /api/service-bot/rsync-excludes?bot_id=xxx&owner_id=xxx
    """
    try:
        # 1. 获取bot信息
        bot = bot_service.get_bot(bot_id=bot_id, user_id=owner_id)
    except Exception as e:
        # Bot不存在时会抛出异常
        if "not found" in str(e).lower() or "不存在" in str(e):
            return ApiResponse(
                success=False,
                message=f"Bot不存在: {bot_id}",
                error_code=404,
                data=None
            )
        # 其他异常返回500
        logger.error(f"[get_bot_rsync_excludes] Error getting bot: {e}")
        return ApiResponse(
            success=False,
            message=f"获取rsync excludes配置失败: {str(e)}",
            error_code=500,
            data=None
        )

    if not bot:
        return ApiResponse(
            success=False,
            message=f"Bot不存在: {bot_id}",
            error_code=404,
            data=None
        )

    try:

        # 2. 解析引擎类型
        engine_type = resolve_engine_for_bot(bot_id, owner_id, bot_repo=bot_repo)

        # 3. 获取引擎默认配置
        from agentclaw.community.core.workspace.engines.openclaw import _OPENCLAW_RSYNC_EXCLUDES
        from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_RSYNC_EXCLUDES

        if engine_type == "openclaw":
            default_excludes = list(_OPENCLAW_RSYNC_EXCLUDES)
        elif engine_type == "claude_code":
            default_excludes = list(_CLAUDE_CODE_RSYNC_EXCLUDES)
        else:
            default_excludes = []

        # 4. 解析Bot自定义配置
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext
        ext = bot.get("ext")
        custom_excludes = parse_build_rsync_excludes_from_ext(ext)

        # 5. 计算合并结果
        merged_excludes = list(default_excludes)
        if custom_excludes:
            for item in custom_excludes:
                if item not in merged_excludes:
                    merged_excludes.append(item)

        # 6. 构造响应
        result = BotRsyncExcludesResponse(
            bot_id=bot_id,
            engine_type=engine_type,
            default_excludes=default_excludes,
            custom_excludes=custom_excludes,
            merged_excludes=merged_excludes,
            excludes_source="default_only" if not custom_excludes else "default_plus_custom"
        )

        return ApiResponse(success=True, data=result.model_dump(), message="OK")

    except Exception as e:
        logger.error(f"[get_bot_rsync_excludes] Error: {e}")
        return ApiResponse(
            success=False,
            message=f"获取rsync excludes配置失败: {str(e)}",
            error_code=500,
            data=None
        )
```

**验证**: 启动服务，检查端点是否存在

---

### 第六步：添加测试用例

#### 6.1 创建 `test_bot_build_service_rsync_excludes.py`

**文件路径**: `src/backend/tests/community/core/service_bot/services/test_bot_build_service_rsync_excludes.py`

**操作**: 创建新测试文件

**内容**: 参考 PR diff 中的测试代码（208 行）

---

#### 6.2 扩展 `test_engine_sandbox_providers.py`

**文件路径**: `src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py`

**操作**: 在文件末尾添加测试类

**内容**: 参考 PR diff 中的测试代码（142 行）

---

#### 6.3 创建 `test_service_bot_rsync_excludes.py`

**文件路径**: `src/backend/tests/community/endpoints/test_service_bot_rsync_excludes.py`

**操作**: 创建新测试文件

**内容**: 参考 PR diff 中的测试代码（271 行）

---

#### 6.4 更新 `test_bot_build_service_openclaw_stage_configs.py`

**文件路径**: `src/backend/tests/community/core/service_bot/services/test_bot_build_service_openclaw_stage_configs.py`

**操作**: 更新 mock 方法签名

**修改点**: 在所有 `DummyProvider.get_build_plan` 方法定义中添加可选参数：

```python
# 找到所有（约 145, 203, 255, 315 行）
def get_build_plan(self) -> EngineBuildPlan:
    return self.plan

# 替换为：
def get_build_plan(self, build_rsync_excludes_append=None) -> EngineBuildPlan:
    return self.plan
```

**验证**: 运行测试 `pytest src/backend/tests/community/core/service_bot/services/test_bot_build_service_openclaw_stage_configs.py -v`

---

## 验证清单

### 功能验证

- [ ] 解析函数正常工作
  - [ ] None 输入返回 None
  - [ ] 空字典返回 None
  - [ ] 缺少键返回 None
  - [ ] 有效配置返回列表
  - [ ] 非字符串项转换为字符串
  - [ ] 过滤无效类型

- [ ] Engine providers 合并逻辑正确
  - [ ] None 参数返回默认配置
  - [ ] 空列表参数返回默认配置
  - [ ] 自定义项正确追加
  - [ ] 重复项正确去重

- [ ] BotBuildService 正确传递配置
  - [ ] 从 bot.ext 读取配置
  - [ ] Noneext 处理正确
  - [ ] 传递给 provider

- [ ] API 端点正常工作
  - [ ] 返回正确的引擎类型
  - [ ] 返回默认 excludes
  - [ ] 返回自定义 excludes
  - [ ] 返回合并后的 excludes
  - [ ] 正确的 excludes_source 标识
  - [ ] Bot 不存在返回 404

### 测试验证

- [ ] 所有新测试通过
  ```bash
  pytest src/backend/tests/community/core/service_bot/services/test_bot_build_service_rsync_excludes.py -v
  pytest src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py::TestRsyncExcludesBotOverride -v
  pytest src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py::TestParseRsyncExcludesFromExt -v
  pytest src/backend/tests/community/endpoints/test_service_bot_rsync_excludes.py -v
  ```

- [ ] 更新的测试通过
  ```bash
  pytest src/backend/tests/community/core/service_bot/services/test_bot_build_service_openclaw_stage_configs.py -v
  ```

- [ ] 无回归问题
  ```bash
  pytest src/backend/tests/community/core/workspace/ -v
  pytest src/backend/tests/community/core/service_bot/ -v
  ```

### 代码质量验证

- [ ] 类型检查通过
  ```bash
  mypy src/backend/src/agentclaw/community/
  ```

- [ ] 代码风格检查通过
  ```bash
  ruff check src/backend/src/agentclaw/community/
  ```

- [ ] 无安全警告
- [ ] 文档字符串完整

---

## 潜在问题及应对

### 问题 1: 导入依赖不存在

**症状**: `ImportError: cannot import name 'parse_build_rsync_excludes_from_ext'`

**原因**: 未按正确顺序修改文件

**解决**: 按照本计划的步骤顺序执行

---

### 问题 2: 测试失败 - Mock签名不匹配

**症状**: `TypeError: get_build_plan() got an unexpected keyword argument`

**原因**: 某个 mock 未更新签名

**解决**: 全局搜索 `def get_build_plan` 确保所有 mock 都已更新

---

### 问题 3: API 端点 500 错误

**症状**: 调用端点返回 500

**原因**: 可能缺少必要的服务注入或依赖

**解决**: 检查日志，确保 `BotServiceProtocol` 和 `BotRepository` 正确注入

---

## 提交信息

```
feat(service-bot): add bot-level rsync excludes configuration

支持通过 bot.ext.build_rsync_excludes 配置自定义 rsync 排除规则。

主要变更：
- 新增 parse_build_rsync_excludes_from_ext 函数解析配置
- 修改 EngineSandboxProvider.get_build_plan 支持自定义 excludes
- 实现合并语义：默认值 + 自定义项（去重）
- 新增 GET /api/service-bot/rsync-excludes 端点查询配置
- 添加完整的单元测试、集成测试和 API 测试

影响范围：
- src/backend/src/agentclaw/community/core/workspace/engines/
- src/backend/src/agentclaw/community/core/service_bot/services/
- src/backend/src/agentclaw/community/adapters/http/service_bot/
- src/backend/tests/community/

参考: PR #491
```

---

## 注意事项

1. **保持代码风格一致**: 遵循项目现有的命名规范、注释风格
2. **类型注解**: 所有新增函数和方法都需要完整的类型注解
3. **文档字符串**: 公共接口需要添加详细的文档字符串
4. **测试优先**: 每完成一个模块，立即编写并运行测试
5. **逐步提交**: 每个步骤完成后，可以先提交到本地，便于回溯
6. **向后兼容**: 所有修改都保持向后兼容，使用可选参数默认值

---

## 时间估算

- 准备工作: 30 分钟
- 核心功能实现: 2 小时
- 测试用例编写: 1.5 小时
- 测试验证与调试: 1 小时
- 代码审查与优化: 30 分钟

**总计**: 约 5-6 小时

---

## 成功标准

✅ 所有测试通过
✅ API 端点正常响应
✅ 配置解析正确
✅ 合并逻辑无误
✅ 代码质量检查通过
✅ 无回归问题
✅ 文档完整