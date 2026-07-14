# 测试用例放置说明

本文档描述 `tests/` 目录的组织结构、命名规范和用例放置规则，帮助开发者正确归类新增测试。

## 目录总览

```
tests/
├── unit/                  # 单元测试（默认运行，无需外部服务）
├── integration/           # 集成测试（需 SQLite，无需远程服务）
├── e2e/                   # 端到端测试（需启动完整应用）
├── architecture/          # 架构合规测试（结构、层级、命名等约束）
├── contract/              # SPI 契约测试（插件实现与 Protocol 接口一致性）
├── utils/                 # 测试工具函数（非测试用例）
└── test_process_manager_env.py  # 独立环境变量测试
```

## 各目录详细说明

### 1. `unit/` — 单元测试

**定位**: 隔离的业务逻辑测试，所有外部依赖通过 mock 替换。运行速度快，不需要任何外部服务。

**放置规则**:
- 目录结构**镜像** `src/secbaas/` 源码结构
- 每个源码模块对应一个同路径的测试目录

**示例映射**:

| 源码路径 | 测试路径 |
|---------|---------|
| `src/secbaas/core/service/bot_manage/` | `tests/unit/core/service/bot_manage/` |
| `src/secbaas/adapters/web/` | `tests/unit/adapters/web/` |
| `src/secbaas/plugins/auth/` | `tests/unit/plugins/auth/` |
| `src/secbaas/infra/utils/` | `tests/unit/infra/utils/` |

**何时放这里**:
- 测试单个函数或类的逻辑，依赖通过 mock 注入
- 测试 Protocol 接口的纯数据行为（如 API model 校验）
- 测试 ORM repository 逻辑（使用 `unit/bootstrap/conftest.py` 提供的内存 SQLite）
- 测试 DI 容器的初始化与 wiring

**典型 conftest 模式**:
- `unit/bootstrap/conftest.py` — 提供 `sqlite_backend`、自动注入 db_manager
- `unit/core/service/conftest.py` — 自动 mock db_manager 防止 "Database not initialized" 错误
- `unit/adapters/web/conftest.py` — 配置 DI 容器，提供 `iter_api_routes` 辅助

---

### 2. `integration/` — 集成测试

**定位**: 使用真实 DI 容器和 SQLite 数据库，验证跨组件协作的正确性。不需要远程服务（ZDAS、ZCache 等）。

**运行方式**: 需显式指定 marker，`uv run pytest tests/integration/ -v -m integration`

**放置规则**:
- 目录结构同样**镜像** `src/secbaas/`
- repository 集成测试按领域拆分：`test_<domain>_repository.py`（通用）和 `test_<domain>_sqlite.py`（SQLite 特定）

**示例结构**:
```
integration/
├── core/
│   ├── repository/          # Repository 集成测试（32 个文件）
│   │   ├── conftest.py      # 动态生成 17 个 Protocol 类型 repository fixture
│   │   ├── test_bot_repository.py
│   │   ├── test_bot_sqlite.py
│   │   └── ...
│   └── service/             # Service 集成测试（12 个文件）
│       ├── conftest.py      # 实体追踪 + 会话级清理
│       └── ...
├── bootstrap/               # 容器初始化与插件选择器测试
└── adapters/web/            # Web 路由集成测试
```

**何时放这里**:
- 需要真实数据库操作验证 SQL/ORM 正确性
- 需要多个 service/repository 协作的测试
- 验证 DI 容器的端到端注入效果

**典型 conftest 模式**:
- 自动使用 `bootstrap` fixture 初始化容器
- 实体追踪 + 会话级批量清理（按外键依赖顺序删除）
- Protocol 类型化的 repository fixture（从 `_DOMAIN_REGISTRY` 动态生成）

---

### 3. `e2e/` — 端到端测试

**定位**: 通过 HTTP 客户端对运行中的应用实例进行完整链路测试。

**前置条件**: 需先启动应用 `just start`，并安装开发依赖 `uv sync --group dev`。

**运行方式**: `uv run pytest tests/e2e/ -v -m e2e`

**放置规则**:
- 按场景组织，**不按源码模块镜像**
- 使用 pytest marker 区分子场景

**目录结构**:
```
e2e/
├── conftest.py              # http_client、APITestHelper、实体追踪与清理
├── crud/                    # 基础 CRUD API 测试
│   ├── test_bot_api.py
│   ├── test_session_api.py
│   └── ...
├── mock_paas_success/       # PaaS 成功场景
│   ├── async/               # 异步 hook 流程（create/destroy/restart/scale 等）
│   └── sync/                # 同步操作
└── mock_paas_failure/       # PaaS 失败场景
```

**何时放这里**:
- 验证完整的 HTTP 请求 → 应用 → 数据库 → 响应链路
- 验证 API 端点的请求/响应格式
- 验证 Hook 回调和异步生命周期流程

**可用 marker**:
- `@pytest.mark.e2e` — 标记为 E2E 测试（必须）
- `@pytest.mark.crud` — CRUD 基础操作
- `@pytest.mark.sync` — 同步操作
- `@pytest.mark.async_hook` — 异步 hook 流程
- `@pytest.mark.mock_paas_hook_failure` — Hook 执行失败场景
- `@pytest.mark.mock_paas_create_failure` — 创建失败场景
- `@pytest.mark.mock_paas_destroy_failure` — 销毁失败场景
- `@pytest.mark.mock_paas_device_not_found` — 设备未找到场景

---

### 4. `architecture/` — 架构合规测试

**定位**: 通过 pytestarch（导入图分析）和 AST 分析，强制执行微内核架构 Constitution 的 25 条规则。

**放置规则**:
- 文件名使用描述性命名，体现被测试的架构规则
- 详见 [`RULES-MANIFEST.md`](architecture/RULES-MANIFEST.md) 获取规则与测试的映射关系

**示例**:
```
architecture/
├── conftest.py                    # 构建 EvaluableArchitecture（会话级 fixture）
├── check_protocols/               # Protocol 静态类型检查（mypy 驱动，非运行时测试）
│   └── api/
│       └── bot_runtime/
│           └── check_bot_runner.py # 验证 BotRunner 实现满足 BotRunner Protocol
├── test_layer_rules.py            # 层级依赖规则
├── test_contract_rules.py         # 契约定义规则
├── test_plugin_isolation.py       # 插件隔离规则
├── test_adapter_thinness.py       # 适配器瘦规则
├── test_naming_conventions.py     # 命名规范
├── test_no_infra_leakage.py       # 基础设施泄漏检测
└── ...
```

**何时放这里**:
- 新增架构约束规则（如禁止跨层直接调用）
- 检测命名规范违反
- 验证模块边界和依赖方向

#### `check_protocols/` — Protocol 静态类型检查

**定位**: 通过 mypy 的结构化子类型检查，验证具体实现类是否满足对应 Protocol 接口定义。**这不是运行时测试，而是编译期类型检查。**

**工作机制**:

1. 在检查文件中，将具体实现类的实例赋值给一个 **Protocol 类型注解** 的变量
2. mypy 在静态分析时，会校验赋值右侧的类型是否结构兼容左侧的 Protocol
3. 如果 Protocol 新增了方法或修改了签名而实现类未同步，mypy 会报类型错误

**示例**（`check_bot_runner.py`）:

```python
from secbaas.api.bot_runtime import BotRunner as BotRunnerProtocol  # Protocol 定义
from secbaas.core.service.bot_run import BotRunner                  # 具体实现

# mypy 会检查 BotRunner 是否满足 BotRunnerProtocol 的所有方法签名
_bot_runner: BotRunnerProtocol = BotRunner(
    bot_service_selector=MagicMock(spec=BotServiceSelector),
    run_repository=MagicMock(spec=BotRunRepository),
    binding_resolver=MagicMock(spec=BotBindingResolver),
    dispatcher=MagicMock(spec=MessageDispatcher),
)
```

**为何使用 MagicMock**: `BotRunner.__init__` 需要真实依赖，通过 `MagicMock(spec=...)` 构造满足类型签名的假对象，使文件能通过 mypy 的构造函数参数检查，同时不引入真实基础设施。

**放置规则**:
- 目录结构**镜像** `src/secbaas/` 中的 Protocol 定义路径，方便定位
- 每个文件以 `check_<protocol_name>.py` 命名
- 文件中 **不包含** pytest 测试函数，仅通过类型注解触发 mypy 检查

**运行方式**: 通过 `just check-protocols`（即 `uv run mypy tests/architecture/check_protocols`）在 CI 中执行，**不**由 `pytest` 运行

**何时新增检查文件**:
- 新增 `typing.Protocol` 定义时，应在对应路径下新增 `check_<name>.py`
- 现有 Protocol 的方法签名变更时，对应的检查文件应同步更新

---

### 5. `contract/` — SPI 契约测试

**定位**: 验证每个 SPI 插件实现满足其 Protocol 接口契约。

**放置规则**:
- 每个插件类型一个文件：`test_<plugin_name>_plugin.py`
- 位于 `contract/spi/` 子目录下

**现有契约测试**:
```
contract/spi/
├── test_cache_plugin.py
├── test_crypto_plugin.py
├── test_database_plugin.py
├── test_docker_sandbox_plugin.py
├── test_identity_plugin.py
├── test_k8s_sandbox_plugin.py
├── test_poolab_sandbox_plugin.py
├── test_runner_plugin.py
├── test_scheduler_plugin.py
└── test_secret_plugin.py
```

**何时放这里**:
- 新增 Protocol 接口时，需同时新增对应契约测试
- 验证 `local/` 和 `prod/` 实现均满足 Protocol 定义
- 参考 [docs/arch/protocol-contract-tests.md](../../docs/arch/protocol-contract-tests.md)

---

### 6. `utils/` — 测试工具

**定位**: 测试辅助函数和工具模块，**不是**测试用例。

**规则**:
- 不包含 `test_*.py` 文件
- 被 conftest 或测试文件导入复用
- 已有：`_web_port.py`（动态加载 Web 端口）

---

## 命名规范

| 项目 | 规范 | 示例 |
|------|------|------|
| 测试文件 | `test_*.py` 前缀 | `test_bot_service.py` |
| 测试类 | `Test*` 前缀 | `TestBotCreation` |
| 测试函数 | `test_*` 前缀，描述行为而非实现 | `test_create_bot_with_valid_data` |
| conftest | `conftest.py`（pytest 约定） | 作用域涵盖同目录及子目录 |
| Marker | 在 `pytest.ini` 和 `pyproject.toml` 中注册 | `@pytest.mark.integration` |

---

## 运行方式

```bash
# 默认运行单元测试（排除 integration 和 e2e）
uv run pytest tests/ -v

# 运行特定类型
uv run pytest tests/unit/ -v                              # 单元测试
uv run pytest tests/integration/ -v -m integration        # 集成测试
uv run pytest tests/e2e/ -v -m e2e                        # E2E 测试（需启动应用）
uv run pytest tests/architecture/ -v                      # 架构测试
uv run pytest tests/contract/ -v                          # 契约测试

# 运行特定模块的单元测试
uv run pytest tests/unit/core/service/bot_manage/ -v

# 运行特定测试类或函数
uv run pytest tests/unit/core/service/bot_manage/test_bot_crud_service.py::TestBotCrudService::test_create_bot -v

# E2E 测试特定场景
uv run pytest tests/e2e/crud/test_bot_api.py -v -m e2e
```

---

## 新增测试的快速决策

```
你要测试什么？
│
├─ 单个函数/类的逻辑，依赖可 mock → unit/
│
├─ 跨组件协作（service + repository），需真实数据库 → integration/
│
├─ 完整 HTTP 链路（请求 → 应用 → 响应） → e2e/
│
├─ 模块层级/依赖方向/命名规范等结构约束 → architecture/
│
├─ 插件实现是否满足 Protocol 接口 → contract/spi/
│
└─ 多个测试共享的工具函数 → utils/
```

---

## 注意事项

1. **默认只跑单元测试**: `pytest.ini` 中 `addopts = -m "not integration and not e2e"`，集成和 E2E 测试需显式指定 marker
2. **E2E 测试需启动应用**: 运行前确保 `just start` 已启动，且 `just test-e2e` 可一键运行
3. **架构规则文档**: 新增架构测试时需同步更新 [`RULES-MANIFEST.md`](architecture/RULES-MANIFEST.md)
4. **conftest 作用域**: `conftest.py` 的 fixture 对同目录及所有子目录生效，注意避免命名冲突
5. **失败重试**: 默认配置 `--reruns 2 --reruns-delay 1`，不稳定的测试会自动重试 2 次