# PR #491 分析与复刻计划

## 一、PR 概览

**PR 标题**: feat: add bot rsync exclude dir
**PR 编号**: #491
**修改文件数**: 11
**代码变更**: +851, -32

### 核心功能

为 Bot 添加 rsync exclude 目录配置功能，支持 Bot 级别的自定义排除规则。

## 二、功能详细说明

### 2.1 业务需求

在 Bot 构建过程中，需要同步文件到工作空间。不同引擎（OpenClaw、ClaudeCode）有默认的 rsync 排除规则（如排除日志、缓存等目录）。

此功能允许 Bot 所有者通过 `ext.build_rsync_excludes` 字段配置额外的排除规则，实现更灵活的文件同步控制。

### 2.2 设计方案

**合并语义**：自定义规则与默认规则合并（去重），而非完全覆盖。

```
最终排除规则 = 引擎默认规则 + Bot 自定义规则（去重）
```

### 2.3 技术实现

#### 数据模型

在 `ac_bots.ext` JSON 字段中添加 `build_rsync_excludes` 数组：

```json
{
  "build_rsync_excludes": ["custom_cache/", "temp_files/"]
}
```

#### API 端点

新增 GET `/api/service-bot/rsync-excludes` 端点，查询 Bot 的 rsync 配置：

**请求参数**:
- `bot_id`: Bot ID
- `owner_id`: Bot 所有者 ID

**响应数据**:
```json
{
  "success": true,
  "data": {
    "bot_id": "xxx",
    "engine_type": "openclaw",
    "default_excludes": ["workspace/memory/", "logs/", ...],
    "custom_excludes": ["custom_cache/"],
    "merged_excludes": ["workspace/memory/", "logs/", ..., "custom_cache/"],
    "excludes_source": "default_plus_custom"
  }
}
```

## 三、修改文件清单

### 3.1 核心代码文件（7个）

#### 1. `router_build.py` (+111行)

**路径**: `src/backend/src/agentclaw/community/adapters/http/service_bot/router_build.py`

**修改内容**:
- 添加 `GET /rsync-excludes` 端点
- 实现 `get_bot_rsync_excludes` 处理函数
- 主要逻辑：
  1. 获取 Bot 信息
  2. 解析引擎类型
  3. 获取引擎默认配置
  4. 解析 Bot 自定义配置
  5. 合并规则并返回

#### 2. `schemas.py` (+10行)

**路径**: `src/backend/src/agentclaw/community/adapters/http/service_bot/schemas.py`

**修改内容**:
- 添加 `BotRsyncExcludesResponse` 模型

```python
class BotRsyncExcludesResponse(BaseModel):
    bot_id: str
    engine_type: str
    default_excludes: list[str]
    custom_excludes: Optional[list[str]]
    merged_excludes: list[str]
    excludes_source: str
```

#### 3. `bot_build_service.py` (+8行, -1行)

**路径**: `src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py`

**修改内容**:
- 在 `build` 方法中解析 `bot.ext.build_rsync_excludes`
- 调用 `parse_build_rsync_excludes_from_ext(ext)`
- 将解析结果传递给 `provider.get_build_plan(build_rsync_excludes_append=...)`

#### 4. `engine_sandbox.py` (+11行, -1行)

**路径**: `src/backend/src/agentclaw/community/core/workspace/engine_sandbox.py`

**修改内容**:
- 修改 `get_build_plan` 方法签名，添加可选参数 `build_rsync_excludes_append`

```python
def get_build_plan(
    self,
    build_rsync_excludes_append: list[str] | None = None,
) -> EngineBuildPlan:
    ...
```

#### 5. `engines/__init__.py` (+30行)

**路径**: `src/backend/src/agentclaw/community/core/workspace/engines/__init__.py`

**修改内容**:
- 添加 `parse_build_rsync_excludes_from_ext` 函数
- 功能：从 `ac_bots.ext` 字段解析 `build_rsync_excludes` 配置

#### 6. `engines/claude_code.py` (+29行, -14行)

**路径**: `src/backend/src/agentclaw/community/core/workspace/engines/claude_code.py`

**修改内容**:
- 提取 `_make_claude_code_build_plan` 工厂函数
- 修改 `get_build_plan` 支持 excludes 合并
- 合并逻辑：默认值 + 自定义项（去重）

#### 7. `engines/openclaw.py` (+27行, -12行)

**路径**: `src/backend/src/agentclaw/community/core/workspace/engines/openclaw.py`

**修改内容**:
- 提取 `_make_openclaw_build_plan` 工厂函数
- 修改 `get_build_plan` 支持 excludes 合并
- 合并逻辑：默认值 + 自定义项（去重）

### 3.2 测试文件（4个）

#### 1. `test_bot_build_service_openclaw_stage_configs.py` (+4行, -4行)

**路径**: `src/backend/tests/community/core/service_bot/services/test_bot_build_service_openclaw_stage_configs.py`

**修改内容**:
- 更新 mock 的 `get_build_plan` 方法签名，添加可选参数

#### 2. `test_bot_build_service_rsync_excludes.py` (+208行，新文件)

**路径**: `src/backend/tests/community/core/service_bot/services/test_bot_build_service_rsync_excludes.py`

**测试场景**:
- 从 `bot.ext` 读取配置并传递给 provider
- 处理 `ext` 为 `None` 的情况
- 处理缺少 `ext` 字段的情况
- 处理空列表的情况

#### 3. `test_engine_sandbox_providers.py` (+142行)

**路径**: `src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py`

**测试场景**:
- OpenClaw 和 ClaudeCode 的默认 excludes
- Bot 级别覆盖与默认值合并
- 去重逻辑验证
- 空值和 None 值处理
- `parse_build_rsync_excludes_from_ext` 函数的各种输入

#### 4. `test_service_bot_rsync_excludes.py` (+271行，新文件)

**路径**: `src/backend/tests/community/endpoints/test_service_bot_rsync_excludes.py`

**测试场景**:
- API 端点逻辑测试
- 不同引擎类型的配置查询
- Bot 不存在的错误处理
- 参数缺失的验证测试

## 四、REL20260730_zq 分支现状分析

### 4.1 目标分支确认

- **分支名**: `REL20260730_zq`
- **基准分支**: `REL20260730` (已同步)
- **最新提交**: `5a60dd4c test(engine): cover finalizing probe permissions`

### 4.2 文件存在性检查

所有需要修改的文件在目标分支中均存在：

```
✓ src/backend/src/agentclaw/community/adapters/http/service_bot/router_build.py
✓ src/backend/src/agentclaw/community/adapters/http/service_bot/schemas.py
✓ src/backend/src/agentclaw/community/core/service_bot/services/bot_build_service.py
✓ src/backend/src/agentclaw/community/core/workspace/engine_sandbox.py
✓ src/backend/src/agentclaw/community/core/workspace/engines/__init__.py
✓ src/backend/src/agentclaw/community/core/workspace/engines/claude_code.py
✓ src/backend/src/agentclaw/community/core/workspace/engines/openclaw.py
```

### 4.3 潜在冲突分析

需要检查目标分支中这些文件的当前状态，确认是否已有类似修改或冲突。

## 五、实施计划

### 阶段一：准备工作

1. **确认分支状态**
   - 确保 `REL20260730_zq` 分支已同步最新代码
   - 确认工作目录干净

2. **代码审查**
   - 检查每个目标文件的当前状态
   - 识别可能的冲突点
   - 确认是否需要调整实现方式

### 阶段二：核心功能实现

**优先级顺序（从基础设施到上层应用）**：

#### 步骤 1: 修改基础设施层

**文件**: `engines/__init__.py`

添加解析函数：
```python
def parse_build_rsync_excludes_from_ext(ext: dict[str, Any] | None) -> list[str] | None:
    # 从 bot.ext 解析 build_rsync_excludes 配置
```

#### 步骤 2: 修改接口层

**文件**: `engine_sandbox.py`

修改接口方法签名：
```python
def get_build_plan(
    self,
    build_rsync_excludes_append: list[str] | None = None,
) -> EngineBuildPlan:
```

#### 步骤 3: 实现引擎层

**文件**: `engines/openclaw.py` 和 `engines/claude_code.py`

1. 提取 build plan 工厂函数
2. 实现 excludes 合并逻辑
3. 更新 `get_build_plan` 方法

#### 步骤 4: 集成到业务层

**文件**: `bot_build_service.py`

1. 解析 `bot.ext.build_rsync_excludes`
2. 调用 parser 函数
3. 传递给 provider

#### 步骤 5: 暴露 API 端点

**文件**: `schemas.py` 和 `router_build.py`

1. 定义响应模型
2. 实现端点处理函数
3. 添加权限拦截器

### 阶段三：测试用例实现

**优先级顺序**：

1. **单元测试** - `test_engine_sandbox_providers.py`
   - 测试 excludes 合并逻辑
   - 测试去重功能
   - 测试 parser 函数

2. **集成测试** - `test_bot_build_service_rsync_excludes.py`
   - 测试 service 层配置传递
   - 测试各种边界情况

3. **API 测试** - `test_service_bot_rsync_excludes.py`
   - 测试端点响应
   - 测试错误处理

4. **兼容性测试** - `test_bot_build_service_openclaw_stage_configs.py`
   - 更新已有测试的 mock 签名

### 阶段四：验证与测试

1. **运行单元测试**
   ```bash
   pytest src/backend/tests/community/core/service_bot/services/test_bot_build_service_rsync_excludes.py
   pytest src/backend/tests/community/core/workspace/test_engine_sandbox_providers.py
   pytest src/backend/tests/community/endpoints/test_service_bot_rsync_excludes.py
   ```

2. **运行集成测试**
   ```bash
   pytest src/backend/tests/community/ -k "rsync" -v
   ```

3. **手动验证**
   - 启动服务
   - 调用 GET `/api/service-bot/rsync-excludes` 端点
   - 验证返回数据格式

### 阶段五：代码审查与提交

1. **代码审查要点**
   - 是否遵循项目编码规范
   - 是否添加了必要的类型注解
   - 文档字符串是否完整
   - 测试覆盖率是否充分

2. **提交信息格式**
   ```
   feat(service-bot): add bot-level rsync excludes configuration

   - Support custom rsync exclude patterns via bot.ext.build_rsync_excludes
   - Add GET /api/service-bot/rsync-excludes endpoint
   - Merge bot excludes with engine defaults (deduplication)
   - Add comprehensive unit and integration tests
   ```

## 六、风险评估

### 6.1 兼容性风险

**风险**: 修改 `get_build_plan` 方法签名可能影响现有调用方

**缓解措施**:
- 使用可选参数，默认值为 `None`
- 保持向后兼容

### 6.2 数据风险

**风险**: Bot 的 `ext` 字段可能包含无效数据

**缓解措施**:
- 添加严格的类型检查
- 处理 None、空列表、非列表类型等边界情况
- 记录警告日志

### 6.3 性能风险

**风险**: excludes 列表合并的去重操作可能影响性能

**缓解措施**:
- 使用列表推导而非 set 保持顺序
- 在一般情况下列表长度有限（< 50），性能影响可忽略

## 七、后续优化建议

1. **配置验证**
   - 添加 excludes 模式格式验证
   - 限制最大条目数
   - 限制字符串长度

2. **监控告警**
   - 添加 metrics 记录自定义配置使用情况
   - 记录配置解析失败事件

3. **文档完善**
   - 更新 API 文档
   - 添加用户手册
   - 补充配置示例

4. **功能增强**
   - 支持正则表达式模式
   - 支持通配符模式
   - 提供配置模板

## 八、总结

PR #491 实现了 Bot 级别的 rsync exclude 配置功能，核心改动：

1. **数据模型**: 在 `ac_bots.ext` 添加 `build_rsync_excludes` 字段
2. **解析逻辑**: 新增 `parse_build_rsync_excludes_from_ext` 函数
3. **合并语义**: 自定义规则与默认规则合并（去重）
4. **API 暴露**: 新增查询端点，返回三部分信息（默认、自定义、合并）
5. **测试覆盖**: 完整的单元测试、集成测试、API 测试

复刻策略：按照基础设施 → 引擎层 → 业务层 → API 层的顺序逐步实现，确保每个层次的正确性和兼容性。