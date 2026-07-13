# Backend Profile / Env 完整分离设计

日期：2026-07-11
状态：设计讨论已确认，书面 Spec 待用户复核

## 1. 背景

Backend 当前同时使用两组概念：

- `DEPLOY_PROFILE` / `DeployProfile`：选择进程采用哪一组实现，例如 `corp`、
  `community`、`test`、`corp_test`、`singlebox`。
- `SERVER_ENV`：表达数据和配置所属环境，例如 `dev`、`pre`、`prod`。

当前实现仍有几处把两者混在一起：

1. singlebox 启动时同时设置 `DEPLOY_PROFILE=singlebox` 和
   `SERVER_ENV=singlebox`。
2. `TestingAccessModule`、`TestHttpClientModule` 在 Provider 内读取
   `SERVER_ENV`，再决定返回测试实现还是真实实现。
3. `YamlConfigProvider` 通过 `SERVER_ENV=singlebox` 选择
   `application-singlebox.yaml`。
4. Device `Env.from_string()` 把 `singlebox` 特判为 `dev`，API Schema 也需要
   兼容这一别名。
5. `get_current_env()` 把 `singlebox` 暴露成第四种业务环境，使业务代码可以继续
   增加 `if env == "singlebox"` 分支。

结果是 Profile 与 Env 互相代偿：只看其中一个无法判断进程究竟安装了什么实现，
也无法确定数据应该落在哪个环境分区。

## 2. 设计目标

建立一条明确且可静态守卫的规则：

> Profile 决定安装哪些实现；Env 只决定同一实现所使用的环境字段和数据分区。

具体目标：

1. `DeployProfile` 是唯一的实现装配选择器。
2. Backend 的标准 Env 只包含 `dev`、`pre`、`prod`；需要区分灰度的接口仍可返回
   `gray`。
3. singlebox 使用 `DEPLOY_PROFILE=singlebox` 和 `SERVER_ENV=dev`。
4. singlebox 仍加载 `application-singlebox.yaml`，但该选择由 Profile 完成。
5. Device Core、Repository 和 HTTP Schema 不再识别 `singlebox` Env 别名。
6. legacy `SERVER_ENV=singlebox` 在 Backend 启动阶段明确失败，不做静默兼容。
7. 业务层和 Core 层不增加 singlebox 条件分支。

## 3. 非目标

本次不做以下工作：

- 不引入 `RuntimeEnvironmentModule`、`DataEnvironment` 或全局
  `DeviceRuntimeEnvironment` 服务。
- 不一次性重写全部 `get_current_env()` 调用；现有调用在 singlebox 下自然得到
  `dev`。
- 不重构 BAAS、Engine、BCS 的环境体系；只验证它们与 Backend 新启动契约兼容。
- 不重新设计整张 DI Profile 矩阵；只拆分当前依赖 Env 再分流的绑定。
- 不改变线上 `dev`、`pre`、`prod` 的实现和数据语义。

## 4. 核心模型

### 4.1 两条正交轴

```text
DEPLOY_PROFILE                         SERVER_ENV
决定 implementation / module          决定 field / data partition

corp       -> corp plugins             dev
community  -> community plugins        pre
test       -> test doubles             prod
corp_test  -> corp test doubles        gray (仅专用查询保留)
singlebox  -> local standalone plugins
```

典型组合：

| 场景 | DEPLOY_PROFILE | SERVER_ENV |
| --- | --- | --- |
| 开源部署 | `community` | `dev` / `pre` / `prod` |
| 单元与组件测试 | `test` | `dev` 或不设置 |
| 蚂蚁测试 | `corp_test` | `dev` 或不设置 |
| 本地 singlebox | `singlebox` | `dev` |
| 蚂蚁线上 | `corp` | `pre` / `prod` |

`singlebox` 是部署形态，不是数据环境。因此 singlebox 创建的 Device 记录、审计记录
和其它环境字段统一写入 `dev`。

物理工作目录不属于数据 Env。singlebox 通过 Profile 对应 overlay 中的
`workspace.env_folder: aidesktop_singlebox` 使用独立目录；Backend 与 BAAS 各自从
singlebox 配置读取同一语义字段。这样数据分区是 `dev`，但本地文件不会与普通 dev
工作目录混用，也不需要额外的进程环境变量。

### 4.2 Composition Root

启动链路在 Composition Root 完成以下步骤：

```text
读取 DEPLOY_PROFILE 一次
        |
        +--> 选择 ConfigProvider / YAML overlay
        |
        +--> modules_for(profile) 选择 DI 实现
        |
        +--> 构建 Injector

SERVER_ENV
        |
        +--> 标准化为 dev / pre / prod
        +--> 供已选中的实现读取环境字段
```

任何 Module Provider 都不得再用 `SERVER_ENV` 判断自己应该返回哪个实现。

## 5. DI 装配设计

### 5.1 Access

当前 `TestingAccessModule` 同时用于 `test` 和 `singlebox`，内部再读取
`SERVER_ENV`：

```text
TestingAccessModule
    +-- SERVER_ENV=singlebox -> LocalPolicyService
    +-- 其他                 -> PolicyService
```

目标设计：

```text
DeployProfile.TEST
    +-- 不安装 Access override
    +-- AccessModule -> PolicyService

DeployProfile.SINGLEBOX
    +-- 安装 SingleboxAccessModule
    +-- PolicyServiceProtocol -> LocalPolicyService
```

改造要求：

- `TestingAccessModule` 改为 `SingleboxAccessModule`。
- `SingleboxAccessModule` 固定返回 `LocalPolicyService`，不读取任何 Env。
- `SingleboxAccessModule` 只出现在 `DeployProfile.SINGLEBOX` 的模块列中。
- `DeployProfile.TEST` 保留真实 `PolicyService`，继续验证白名单和 quota 逻辑。
- `UserService`、`PolicyRepository` 及 Router 的注入协议不变。

### 5.2 HTTP Client

当前 `TestHttpClientModule` 同时服务 test 与 singlebox，并在每个 Provider 内根据
`SERVER_ENV` 返回 `LocalHttpClient` 或 `HttpxClient`。

目标设计：

- `DeployProfile.TEST` 和 `DeployProfile.CORP_TEST` 安装
  `TestHttpClientModule`，它固定返回禁止意外出网的 `LocalHttpClient`。
- `DeployProfile.SINGLEBOX` 不安装 HTTP override，沿用基础
  `HttpClientModule` 提供的真实 `HttpxClient`，请求本地 BAAS/BCS 服务。
- `TestHttpClientModule` 删除全部 `SERVER_ENV == "singlebox"` 分支。

### 5.3 Profile 模块列表

`_common_test_doubles()` 只保留 test、corp_test、singlebox 真正共享的实现。
Access 和 HTTP Client 从共享列表移出，并由具体 Profile 分支追加：

| Profile | Access binding | HTTP binding |
| --- | --- | --- |
| `test` | `PolicyService` | `LocalHttpClient` |
| `corp_test` | `PolicyService` | `LocalHttpClient` |
| `singlebox` | `LocalPolicyService` | `HttpxClient` |

其它暂时共享的 local/test doubles 不在本次拆分范围内。

## 6. 配置选择

YAML overlay 的选择从 Env 分流改为 Profile 分流：

| Profile | Config provider / overlay |
| --- | --- |
| `community` | `application-community.yaml` |
| `test` / `corp_test` | `application-test.yaml` |
| `singlebox` | `application-singlebox.yaml` |
| `corp` | corp `ConfigProvider`，内部按 Env 读取环境字段 |

`register_config_provider(profile)` 在配置首次读取前注册已经带有 Profile 选择结果的
Provider。`YamlConfigProvider` 不再通过 `SERVER_ENV=singlebox` 猜测部署形态。

因此 singlebox 可以同时满足：

```text
DEPLOY_PROFILE=singlebox  -> application-singlebox.yaml
SERVER_ENV=dev            -> 数据和业务环境字段为 dev
```

## 7. Env 与 Device 语义

### 7.1 Env 工具

- `get_current_env()` 只返回 `dev`、`pre`、`prod`。
- `get_current_env_with_gray()` 可继续返回 `gray`，用于已有灰度业务。
- 原始 `stable` 继续归一到 `dev`，`prepub` 继续归一到 `pre`，`gray` 继续归一到
  `prod` 或由 gray 专用接口保留。
- 删除 `get_current_env()` 和 `is_dev()` 对 `singlebox` 的特殊分支。
- `is_local_mode()` 仍是 Profile 维度的过渡工具，不读取 Env。
- 当前无生产调用方的 `is_singlebox()` 删除；后续若确需识别部署形态，应通过
  Composition Root 选择实现，而不是读取 Env。

### 7.2 Device

Device `Env` 严格接受 `dev`、`pre`、`prod`：

- `Env.from_string("singlebox")` 必须抛出 `ValueError`。
- singlebox 启动时 `SERVER_ENV=dev`，所以创建和查询 Device 自然使用 `Env.DEV`。
- 删除 Schema 层 `singlebox -> dev` alias。
- Repository 继续按 `dev`、`pre`、`prod` 存取，不引入新字段和数据迁移。

singlebox SQLite 数据是本地临时数据，因此已有 `env=singlebox` 数据不做迁移；重新
启动后按 `dev` 重新生成。线上数据语义不变。

## 8. 启动与兼容策略

`scripts/modules/backend.sh` 的 singlebox Backend 启动参数改为：

```bash
DEPLOY_PROFILE=singlebox \
SERVER_ENV=dev
```

物理目录由 Backend 与 BAAS 的 singlebox overlay 声明：

```yaml
workspace:
  env_folder: aidesktop_singlebox
```

所有 Backend 启动入口都在读取配置和构建 Injector 前检查 legacy 配置；无论使用
哪个 Profile，原始 Env 中的 `singlebox` 都不是合法输入：

```text
SERVER_ENV=singlebox
    -> 启动失败
    -> 提示使用 DEPLOY_PROFILE=singlebox SERVER_ENV=dev
```

不提供静默 fallback。这样旧脚本不会表面启动成功、实际加载错误实现或错误数据分区。

外层 OCB 或其它启动器若仍设置 `SERVER_ENV=singlebox`，必须在采用此 Avernet commit
前同步修改。BAAS 的 `--singlebox`、Engine 和 BCS 可保留各自模式参数，只需确保传给
Backend 的 Env 是 `dev`。

## 9. 静态守卫

新增架构测试，扫描 Backend 生产代码并阻止以下模式重新出现：

```text
SERVER_ENV == "singlebox"
get_current_env() == "singlebox"
Env.from_string("singlebox")
```

允许出现 `singlebox` 的位置包括：

- `DeployProfile.SINGLEBOX` 和 Composition Root 的 Profile 分支。
- Composition Root 对 legacy `SERVER_ENV=singlebox` 的拒绝与迁移报错。
- `SingleboxAccessModule` 等明确由 Profile 安装的实现名称和说明。
- singlebox 脚本、测试、配置文件名及覆盖率工具。

守卫的目标不是禁止 singlebox 概念，而是禁止把它重新塞回 Env 轴。

## 10. 迁移顺序

1. 先补充 Profile、配置、Access、HTTP Client 和 Env 的行为测试。
2. 让 ConfigProvider 根据 Profile 选择 overlay。
3. 拆分 `SingleboxAccessModule` 与 `TestHttpClientModule` 装配。
4. 修改 singlebox Backend 启动契约为 `SERVER_ENV=dev`。
5. 收紧 Env 工具与 Device `Env`，删除 Schema alias。
6. 增加 legacy fail-fast 和静态架构守卫。
7. 运行 Backend 全量测试、真实 singlebox 回归及跨系统兼容验证。

这一次序保证每一步都能用明确的绑定矩阵和启动行为验证，而不是依赖最终 E2E 才发现
装配漂移。

## 11. 验证方案

### 11.1 单元与架构测试

- `DeployProfile.detect()` 对五种合法 Profile 和非法输入的测试。
- 配置矩阵测试，证明各 Profile 固定加载预期 overlay。
- Injector 绑定矩阵测试：
  - test -> `PolicyService` + `LocalHttpClient`
  - corp_test -> `PolicyService` + `LocalHttpClient`
  - singlebox -> `LocalPolicyService` + `HttpxClient`
- `Env.from_string()` 只接受 `dev`、`pre`、`prod`。
- `get_current_env()` 在 singlebox 启动契约下返回 `dev`。
- legacy `SERVER_ENV=singlebox` 启动失败并给出迁移提示。
- 静态守卫证明生产代码没有 Env 维度的 singlebox 分支。

### 11.2 Backend 回归

- 运行 `tests/community` 全量测试，要求无失败、无意外跳过。
- 重点回归 Access 白名单/quota、HTTP Client 防出网和 Device CRUD。
- 回归 PR #62 涉及的 Device API，确认返回 `env=dev` 且无需 Schema alias。

### 11.3 真实 singlebox

- 使用仓库 `scripts/singlebox.sh` 启动完整栈。
- 直接检查 Backend、BAAS、BCS、Frontend 的进程、端口和 health endpoint。
- 执行 Backend acceptance / E2E，确认：
  - 空白名单不会阻断本地用户。
  - Backend 真实访问本地 BAAS/BCS。
  - Device 创建、查询和持久化的 Env 为 `dev`。
  - Backend 与 BAAS 均使用 `aidesktop_singlebox` 物理工作目录。
  - `application-singlebox.yaml` 仍被加载。
- 停止并重新启动，确认没有依赖 legacy `SERVER_ENV=singlebox` 的隐藏路径。

### 11.4 跨系统兼容

- BAAS `--singlebox` 启动与 E2E 不受影响。
- Engine 使用现有 dev/local 配置正常启动。
- BCS 继续使用独立的 `BCS_SERVER_ENV`，不被 Backend Env 改造影响。
- 外层 OCB 启动脚本与 Avernet gitlink 联调通过后再更新依赖。

## 12. 成功标准

满足以下条件才视为改造完成：

1. singlebox 启动契约只有 `DEPLOY_PROFILE=singlebox SERVER_ENV=dev`。
2. Access、HTTP Client 和 Config 的实现选择不再依赖 Env。
3. Device 及业务环境值中不再产生 `singlebox`。
4. legacy 配置明确失败，错误信息可直接指导迁移。
5. Backend 全量测试、架构测试和真实 singlebox E2E 全部通过。
6. BAAS、Engine、BCS 兼容验证通过，线上 Profile/Env 行为无变化。
