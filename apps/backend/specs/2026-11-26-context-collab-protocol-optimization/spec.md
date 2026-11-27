# Context Collab 领域对象与 ProductProfileService 改造方案

- 日期：2026-11-26；修订：R3，在 R2 对象及五方法方案上，按用户要求保留 `MethodCompositionTemplate`。
- 当前状态：**R3 新域主路径已实现，本地专项及完整 Backend CI/coverage 已通过；旧产品 facade/存量历史的全量切换未完成，真实部署尚未验证。** 逐项交付及实际结果见 [delivery.md](delivery.md)。下文保留 R3 原设计及原方案阶段的范围记录，不将设计草图当作部署事实。
- 拥有模块：Backend `core/task/context_collab`，重点是 L6 `l6_products/product_profile_service`。
- 源码基线：`f181311410`，分支 `feat/REL20261127_bbs_1`；不代表部署版本。
- 输入：用户指定的《协议层核心接口及领域对象》架构图及本轮修改要求。原图地址、原图文件和运行凭据不进入公开仓库；下文用通用示例说明其对象及配置结构。
- 配套：[实施顺序与验收](plan.md)、[源码证据及修订记录](research.md)。
- 当前契约：[协作域](../../src/agentclaw/community/core/task/context_collab/README.md)、[L5](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md)、[L6](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md)、[当前 Swarm 执行](../../src/agentclaw/community/core/task/context_collab/l5_methods/SWARM-EXECUTION.md)。下文是 R3 的**目标合同与原设计记录**；已实施部分及兼容例外以 [交付差异](delivery.md) 和 owning guide 中的源码/测试证据为准，不能由方案推断全量迁移或部署完成。
- 必须遵守：[架构宪法](../../../../docs/arch/arch.rules.md)、[CI](../../../../docs/arch/ci.enforce.md)、[边界格式](../../../../docs/arch/context-boundary-format.md)、[协议测试](../../../../docs/arch/protocol-contract-tests.md)。不以业务模型调整豁免分层、鉴权或消费者合同测试。

## 1. 本轮确定的修改

1. **删除 `ProductProfileDefinition`，由 `ProductProfile` 承载产品配置定义。** 成员固定为 `id`、`name`、`composition: MethodCompositionTemplate`、`stages`。`composition` 保留 Method 组合模板；`stages` 仍是 YAML 阶段配置，二者并存，不恢复 Definition。
2. **修改 `Collet`**，成员固定为 `id`、`profile_id`、`content`、`status`。
3. **新增独立领域对象 `ColletRollout`、`StageRollout`**，不是只给现有 Plan/Stage 换查询名称。
4. **修改 `Artifact`**，成员为 `type`、`name`、`content`；新增 `ArtifactContent` 及 File、Link、Text 三个子类。
5. **新增 `ProgressEvent`、`AcceptanceResult`**，字段按图定义。
6. **L6 新增 `product_profile_service` 模块，实现 `ProductProfileService`**，对外提供且仅提供本轮指定的五个业务方法：`create_product_profile`、`create_product_collet`、`query_collet_rollout`、`interactive`、`query_stage_rollout_details`。

初稿的“保留 ProductProfileDefinition”“YAML 仅是导入格式”“Rollout 仅是 Plan 视图”“Artifact 不承载内容”“只经 TaskCommissionServiceProtocol 暴露能力”不再作为目标设计，替换关系记录在 [research.md](research.md)。底层执行、权限与持久化能力可复用，但不能因此保留另一套对等的产品领域模型。

## 2. 目标领域对象：字段逐项对齐

以下是 Python 类型草图，不是已经写入源码的实现。`list` 对应图中的 List；`YAML = str` 表示 YAML 文本。对象按图示成员及本轮明确保留的 `ProductProfile.composition` 定义；`MethodCompositionTemplate` 复用现有 L6 类型，不新建同义模板。tenant/env、owner、版本、幂等、运行尝试和外部资源定位等技术信息放在仓储记录/请求上下文中，不偷偷扩充这些对象。

```python
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

YAML: TypeAlias = str


class StatusEnum(str, Enum):
    CREATE = "CREATE"
    RUNNING = "RUNNING"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_FAILED = "RUN_FAILED"
    ACCEPTANCE_SUCCEED = "ACCEPTANCE_SUCCEED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"


class EventTypeEnum(str, Enum):
    RUN_FAILED = "RUN_FAILED"
    ACCEPTANCE_SUCCEED = "ACCEPTANCE_SUCCEED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"


class AdjustTypeEnum(str, Enum):
    DO_NOTHING = "DO_NOTHING"
    OPTIMIZE = "OPTIMIZE"
    RE_DO = "RE_DO"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class ProductProfile:
    id: int
    name: str
    composition: MethodCompositionTemplate
    stages: YAML


@dataclass
class Collet:
    id: int
    profile_id: int
    content: str
    status: StatusEnum


@dataclass
class ArtifactContent:
    """无公共数据字段的内容基类；运行时只接纳以下三个具体子类。"""


@dataclass
class ArtifactContentFile(ArtifactContent):
    storage_type: str
    storage_path: str
    file_name: str
    file_type: str
    file_content: str


@dataclass
class ArtifactContentLink(ArtifactContent):
    link: str
    desc: str


@dataclass
class ArtifactContentText(ArtifactContent):
    text: str


@dataclass
class Artifact:
    type: str
    name: str
    content: ArtifactContent


@dataclass
class AcceptanceResult:
    condition: str
    passed: bool
    reason: str


@dataclass
class ProgressEvent:
    type: EventTypeEnum
    stage_rollout_id: int
    artifacts: list[Artifact]
    acceptances: list[AcceptanceResult]
    error_msg: str


@dataclass
class StageRollout:
    name: str
    stage_rollout_id: int
    status: StatusEnum
    assignee_type: str
    assignee_uuid: str
    input: str
    session_id: str
    artifacts: list[Artifact]
    acceptances: list[AcceptanceResult]
    error_msg: str


@dataclass
class ColletRollout:
    collet_id: int
    rollout_id: int
    stages: list[StageRollout]
    content: str
    status: StatusEnum


@dataclass
class InteractiveResult:
    type: AdjustTypeEnum
    message: str
```

### 2.1 ProductProfile

- 是配置定义、存储、读取和服务返回的统一领域对象；不保留 `ProductProfileDefinition` 包装对象或永久别名。
- `id` 为正整数，由仓储分配；`name` 为产品配置名称；`composition` 为必填的 Method 组合模板；`stages` 保存下面定义的 YAML 文本。
- `composition` 复用 [现有 MethodCompositionTemplate](../../src/agentclaw/community/core/task/context_collab/l6_products/contracts.py) 的 `required_methods`、`optional_methods`、`relations`：声明使用哪些 MethodProfile、各自的 MethodPolicy 和 Method 间组合关系。`relations` 是 Method 层关系，不等同于 stages 内的阶段依赖。required methods 决定实际 Method 组合；optional methods 不因 YAML 出现候选或阶段而隐式启用。
- 首版创建后不可原地修改配置；变更配置创建新 `ProductProfile.id`，既有 Collet 继续引用原 `profile_id`。不额外向对象添加 version 字段。
- 当前 `profiles.py` 已有同名字符串枚举 `ProductProfile`，须重命名为只在兼容边界使用的 `LegacyProductProfileKey` 等明确名字，并迁移导入，不能与新领域类混用。
- 原 Definition 的构建、校验、序列化、持久化与读取职责改用此对象，`composition` 明确保留并完整序列化；role、governance、compatibility 等其余旧成员不因此自动恢复。执行能力选择由组合中的显式 MethodPolicy 及已有能力合同承接，安全约束仍由既有策略/绑定/配置合同执行；未声明 policy 不等于开放执行或录取权限。

### 2.2 Collet、ColletRollout、StageRollout

- `Collet` 表达一次产品协作请求：`profile_id` 指向 ProductProfile，`content` 是本次请求正文，`status` 是本次协作的业务状态。
- 一个 Collet 可有多个 ColletRollout，每次首次执行或调整重跑创建新的 `rollout_id`；`content` 固定该轮完整执行上下文，而不是对话日志。
- 每个 StageRollout 是某一轮的阶段执行事实，具有独立正整数 ID；同名阶段重跑必须生成新 ID，保留旧执行记录和产物。
- `StageRollout.name` 对应 YAML 节点名称；`assignee_type/assignee_uuid` 保存实际执行者，不保存候选集合；`input` 保存该次执行的冻结输入。
- `session_id` 保存执行适配器确认的**真实会话 ID**，不是 `swarm:{binding}:{stage}` 逻辑事件通道。未创建会话时用空字符串；真实会话创建后持久化，再允许按它查会话。
- `artifacts/acceptances` 初始为空列表；没有运行错误时 `error_msg=""`。不把图中必填字符串改成可空字段。
- parent rollout、节点依赖、对应 L4 Task/L5 Allocation、租约、会话 provider/owner/run 身份等关系保存在内部记录。不得通过向 StageRollout 再加一套业务字段绕开图示模型。
- 新整数领域 ID 不强行等于旧字符串 Plan/Stage/Task ID；如复用旧执行链路，建立显式作用域映射。不能使用 `int(old_id)`、哈希碰撞式转换或“当前列表位置”作为迁移 ID。

### 2.3 ArtifactContent 与序列化

- 三类内容对象均是真正的领域值，不再只是展示 DTO。`ArtifactContentFile.file_content`、Link/desc、Text/text 均按图保留。
- `Artifact.type` 的业务语义由产品解释，例如文档或 PR；不擅自把它固定为文件扩展名或要求必须是 file/link/text。
- 内容解码采用封闭子类集合，依据三个互斥字段集合识别 File/Link/Text，拒绝混合字段、未知子类和任意类反序列化，不增加图外的隐式 `content_type` 字段。
- 文件存储和实际读取继续经过受控资源能力；File 可内嵌受限的 `file_content`，大文件通过 `storage_type/storage_path` 定位，内容留空时由存储类型明确其为外部文件。限制体积、拦截凭据/路径越权，不直接在业务日志打印正文。
- 如底层受管工作流仍需 ResourceRef/不可变版本，adapter 将新 Artifact 映射到内部资源证据记录。旧资源定位/授权仍然有效，但不再成为新 Artifact 的公开成员；无法证明资源版本/权限时拒绝，不伪造通过。

## 3. ProductProfile.stages 的 YAML 合同

**Method 组合和阶段配置分别由 `composition` 与 `stages` 承载。** 调用者同时提供 `MethodCompositionTemplate` 与 YAML：前者定义 Method 组合及显式策略，后者是阶段 DAG 的唯一配置源。`ProductProfile.stages` 是一个完整 YAML 文档字符串，顶层包含 `stages` 列表；数据库将完整 composition 与 YAML 文本作为同一份不可原地修改的 Profile 保存，并保留内部校验元数据。不要求调用者另构造 SwarmWorkflowDefinition，也不从 YAML 反推 MethodTemplate。

以下沿用图示结构，候选身份使用通用示例：

```yaml
stages:
  - name: requirement_analysis
    candidates:
      - type: bot
        uuid: "example-bot-01"
      - type: bot
        uuid: "example-bot-02"
    depends_on: []
    acceptance:
      condition: 包含功能描述、约束条件和验收标准的需求说明文档

  - name: implementation
    candidates:
      - type: bot
        uuid: "example-bot-01"
      - type: bot
        uuid: "example-bot-02"
    depends_on:
      - requirement_analysis
    acceptance:
      - condition: 本次需求对应的代码变更 PR，需关联具体代码版本
```

校验与运行语义：

1. 安全解析 YAML，拒绝重复 key、自定义标签、未知字段、超限输入和环；不执行 YAML 内的代码、表达式或类构造。
2. `stages` 非空；`name` 非空且在 Profile 内唯一；依赖引用该名称，不强制新增图外 node_id。
3. `candidates` 为非空列表，元素仅 `type/uuid`；检查重复候选、缺失依赖、自依赖和 DAG 有环等错误。配置候选不自动授予参与/资源权限。
4. 图示对 `acceptance` 同时使用单个 mapping 和列表；兼容两种形式，编译时统一为非空条件列表。不要求用户补 outputs、I/O ref 或 completion_contract_ref 才能使用该 YAML。
5. 解析/编译结果仅为内部执行计划，不是第二个可独立修改的 ProductProfileDefinition。编译器可复用拓扑校验，但必须支持此 schema；旧编译器无法直接处理时显式改造，不填“总是通过”的完成合同。
6. 调度只选择依赖阶段已验收成功的节点，实际执行者从合法候选中选定并冻结；输入由本轮 content、前驱产物及明确接受的干预指令生成。
7. 首版串行调度。acceptance 是供受信验证能力解释的条件，不是 Python 表达式，也不因为执行器正常返回就自动通过。
8. 联合校验 composition 与 stages：校验 MethodProfile、policy 及 relations 合法性，阶段执行/验收要求必须由实际选用的 required methods 及注入能力承接。不支持的组合或缺失能力明确拒绝，不能暗加 Swarm/Open、隐式启用 optional methods 或删除策略限制使其通过。
9. 不在 composition 中另存一份可独立修改的阶段 DAG。现有 `SwarmWorkflowPolicy.workflow` 与此目标有重叠，迁移时应提取为 stages YAML，并将执行策略适配为引用/使用该 YAML 的内部编译结果；如新旧两处图配置不能证明一致，拒绝迁移或创建并要求显式处理，不能静默以任一方覆盖另一方。内部生成的 typed DAG 仅是只读编译结果，不成为第二个配置入口。

## 4. 状态及 ProgressEvent 接纳

### 4.1 状态语义

本轮目标中，Collet、ColletRollout、StageRollout **正式使用同一 StatusEnum**；不再仅把它作为旧模型的显示映射。但三种对象仍有各自的状态聚合与迁移规则，必须写成合同测试。验收成功的枚举拼写按原图固定为 `ACCEPTANCE_SUCCEED`，不自行改成 `ACCEPTANCE_SUCCEEDED`。

- CREATE：请求/轮次/阶段已创建，尚未开始。
- RUNNING：真实执行已开始，或该聚合中有阶段在执行/尚待推进。
- RUN_FINISHED：执行完成、验收尚未确定；正常的 dispatch 返回不算完成，需要真实运行终态及对应执行证据。
- RUN_FAILED：执行层失败。
- ACCEPTANCE_SUCCEED：该层级约定的验收通过。
- ACCEPTANCE_FAILED：该层级约定的验收不通过。

StageRollout 依据受信运行事实进入 RUNNING/RUN_FINISHED，再按 ProgressEvent 验收或失败；没有自动重试。ColletRollout 在任一运行失败时聚合 RUN_FAILED，任一验收失败且无运行失败时聚合 ACCEPTANCE_FAILED，全部阶段验收成功时才为 ACCEPTANCE_SUCCEED，其余依据执行进度为 CREATE/RUNNING/RUN_FINISHED。

Collet 的运行状态跟随内部固定的当前轮次；新轮次可使其重新进入 CREATE/RUNNING，而旧轮次永不被覆盖。Collet 的最终 ACCEPTANCE_SUCCEED 仍须满足产品整体验收合同；如当前产品要求根 Task 由 owner 接受，则所有阶段成功只能说明轮次通过，不能代替根成果验收。新产品若没有额外整体验收，其策略必须显式声明，不能偷偷自动接受旧产品根 Task。

### 4.2 ProgressEvent 与 AcceptanceResult

`ProgressEvent` 字段和 EventTypeEnum 保持图示定义；不新增 RUN_STARTED/RUN_FINISHED 枚举项或把 report_id、sequence、allocation 等直接加到事件对象。必要的身份、幂等键、版本及执行代次由可信入口上下文/内部回执记录承载。

| type | 必要校验与效果 |
| --- | --- |
| RUN_FAILED | `error_msg` 非空；产物可为空；接纳并持久化失败事实，阻断后继执行，不写成验收成功。 |
| ACCEPTANCE_SUCCEED | `acceptances` 覆盖本阶段全部条件且全部 passed；受信校验产物及条件证据后持久化，推进阶段及依赖就绪。 |
| ACCEPTANCE_FAILED | 至少一项 passed=false 且 reason 非空；保存产物/失败理由，不触发后继或自动重跑。 |

`AcceptanceResult.condition` 必须对应冻结 YAML 的条件，`passed` 严格为 bool，`reason` 可解释结果。执行者提交的验收声明不天然具有验收权限，由既有 G1/产品策略和验证能力确认；不能靠正文传 actor 冒充验证者。

事件接纳先解析 StageRollout 所属的 Collet/轮次和实际分配，核验可信身份、tenant/env、当前轮次、尝试及租约，再按回执和版本 CAS 写入。已接纳同请求重放返回原结果；异内容同键冲突；未见过的旧轮次/撤销尝试迟到事件拒绝。合法的业务失败返回“失败事实已接纳”，不因失败类型丢弃事件。

**进度上报仍是执行链路已有/待改造的独立 ingress，不作为 ProductProfileService 的第六个公共方法。** 图中的 `event_report(ProgressEvent)` 由 L5 执行上报能力承接；G3 遥测继续与此分离。

## 5. L6 新模块与五个方法

### 5.1 模块与依赖方向

建议新增以下模块；路径均为待实施设计，不是当前已存在文件：

```text
core/task/context_collab/
  l4_context_primitives/
    collet.py                     # 改成图示 Collet
    artifact.py                   # 新 Artifact 与三个内容子类
    acceptance_result.py          # 低层可共享的验收值
    progress_event.py             # 事件值，不依赖 L6
    product_status.py             # 共享枚举值
  l5_methods/
    collet_rollout.py              # ColletRollout
    stage_rollout.py               # StageRollout
    ...                           # 调度/接纳/修订仍归 L5
  l6_products/
    product_profile.py            # 唯一 ProductProfile 定义
    product_profile_service/
      __init__.py                 # 导出 ProductProfileService
      contracts.py               # ProductProfileServiceProtocol 与响应合同
      service.py                 # ProductProfileService 的五个方法
      stages_yaml.py             # 安全解码与校验/编译适配
      ports.py                   # 窄存储/轮次/交互/会话读取合同
```

L6 协调 Profile、Collet 创建、L5 轮次、交互和会话查询，不直接实现第二套状态机。L4 只保存整数 profile_id，不回调/导入 L6 校验 Profile；有效性先由 L6 检查，再经受控存储提交。

Delivery 依赖显式 `ProductProfileServiceProtocol`，在 `api/task/` 重导出；DI 注入 `ProductProfileService`。需要替换的真实会话、执行或资源能力通过已有 Protocol 或满足 `plugin_api/task/` 分层的窄合同接入，Core 不 import HTTP adapter。旧 TaskCommission facade 迁移为兼容调用者，不继续作为新产品能力的唯一入口。

### 5.2 服务签名草图

2026-11-26 按本轮用户要求精简接口：五方法及对应 HTTP 正文不再接收 `context`、`request_id`。原版显式参数及客户端幂等设计被本节修订替代，原实施验证记录保留在交付报告中。网络相关调用仍采用 async；可信身份由入口建立的 G1 `actor_context_scope` 提供，无身份作用域时拒绝，不能默认使用已存 owner。仓储 tenant/env 继续从可信作用域解析，不能接受正文覆盖。后续按用户追加要求，`interactive` 在 `interactive_context` 前新增 `interactive_uuid` 并移除公开 `expected_revision`；仓储 revision/CAS 保留为内部并发围栏，不是领域对象字段。

```python
class ProductProfileServiceProtocol(Protocol):
    async def create_product_profile(
        self, name: str, composition: MethodCompositionTemplate, stages: YAML,
    ) -> ProductProfile: ...

    async def create_product_collet(
        self, profile_id: int, content: str,
    ) -> Collet: ...

    async def query_collet_rollout(
        self, collet_id: int, all_rollout: bool = True,
    ) -> list[ColletRollout]: ...

    async def interactive(
        self, collet_rollout_id: int, interactive_uuid: str, interactive_context: str,
    ) -> InteractiveResult: ...

    async def query_stage_rollout_details(
        self, stage_rollout_id: int, *,
        limit: int = 50,
    ) -> StageRolloutDetails: ...
```

`ProductProfileService` 实现上述 Protocol。HTTP 在共享认证依赖中绑定并清理身份作用域，Core 不解析 HTTP；库调用方也必须建立经过认证的作用域。创建操作的回执标识内部生成，不承诺创建重试去重，也不按内容偷偷去重。干预通过调用者提供的 `interactive_uuid` 重放已提交结果；revision 从服务读取的快照捕获并在仓储执行 CAS，不要求调用者提供。领域对象本身不添加 version；接口 Envelope 的追踪元数据与本次移除的业务请求参数无关。独立 Bot 进度上报的 ReportContext 及其 request_id/重放合同不变。

### 5.3 create_product_profile

1. 要求可信身份作用域，校验配置管理权限、name 和必填的 composition；验证其 MethodProfile、MethodPolicy、required/optional methods 及 relations，安全解析并验证 stages YAML。
2. 联合校验 composition 与 YAML：执行/验收能力须由实际选用的 Method 组合及注入能力承接，图配置冲突显式拒绝；不存在能力时不能创建一个声称可运行的配置。结构校验不访问真实 Bot 或暗中授权。
3. 创建并原子持久化 `ProductProfile(id, name, composition, stages)`，返回包含完整 composition 及原 YAML 的已落库对象；每次调用创建独立身份，即使名称/composition/stages 完全相同也不复用旧对象。内部回执与完整配置同事务保存。
4. 新流程不再“先返回 Definition 再由调用者额外 publish 才能查到”。这是对旧纯 authoring/独立发布合同的明确替换，须同时迁移调用者及发布授权，**不是绕过配置管理权限**。
5. 返回成功不创建 Collet、不执行阶段、不修改既有配置；不继续提供含不同字段的另一个 Definition 入口。

### 5.4 create_product_collet

1. 精确加载授权作用域内的 ProductProfile，校验创建权限及本次 content。
2. 创建 `Collet(id, profile_id, content, CREATE)`。
3. 按已冻结的 `profile.composition` 初始化所需 MethodBinding 及显式 Method 关系，不隐式选中 optional methods；调用内部轮次能力，按 `profile.stages` YAML 创建首个 ColletRollout 与全部 StageRollout。初始状态为 CREATE，产物/验收为空，未分配和未创建会话字段为空字符串。
4. 同一提交单元保存 Collet、Method 绑定/关系、首轮、阶段、创建回执与待推进意图，然后返回 Collet；外部派发/会话创建在事务后进行，不能在 SQL 长事务里等待 Bot。
5. 首轮执行推进时固定候选、实际执行者和输入，持久化真实 session_id。没有配置的执行能力明确失败，不用本地生成字符串假装创建真实会话。

图中的 `create_collet_rollout` 作为该方法及 interactive 调用的**内部能力**，不增加第六个公共服务方法。每次 create 调用创建独立的 Collet/初轮；内部推进仍使用已存 attempt/回执，不把外部派发重试等同于再次 create。

### 5.5 query_collet_rollout

- 按 `collet_id` 校验读取权限并查询该 Collet 的轮次及其 StageRollout。
- `all_rollout=True`：按创建序返回全部轮次；`False`：只返回内部明确标记的当前轮次，结果仍是 list。
- 真实不存在的 Collet 报 not found；存在但没有轮次返回空列表，不能读时自动创建/重跑。
- 返回阶段执行者、输入、真实 session_id、Artifact、AcceptanceResult 和 error_msg，不在这个接口中拉取会话消息全文。
- 当前轮次标记、创建序及版本是内部存储/响应元数据，不向对象添字段；不能从最大外部 ID 或数组尾部猜当前轮次。历史多时可后续演进分页，首版不声称已有未定义的 cursor 能力。

### 5.6 interactive

1. 精确签名为 `interactive(collet_rollout_id, interactive_uuid, interactive_context)`。`interactive_uuid` 是调用者生成的干预唯一 ID，非空白字符串且最多 128 UTF-8 字节；HTTP 同步接收该字段，不再接收 `expected_revision`。
2. `collet_rollout_id` 精确定位 `ColletRollout.rollout_id`，先校验所属 Collet 和当前操作者权限，再按可信 tenant/env/actor 读取 UUID 回执。同 UUID、同 rollout ID 和反馈文本返回原 `InteractiveResult`，即使当前轮次/状态已经改变也不再次提议、生成轮次或取消/推进意图；同 UUID 改内容或改 rollout 显式冲突。无回执的新干预必须指向当前轮次。读取阶段状态、产物、验收失败及需要的授权会话信息，将 interactive_context 交给只产生建议的注入能力，产生经过 schema 校验的调整建议。
3. 只接纳 DO_NOTHING / OPTIMIZE / RE_DO / SHUTDOWN，返回 `InteractiveResult(type, message)`。自然语言不能成为直接写库的命令；模型不决定授权。
4. DO_NOTHING 留痕但不新建轮次；OPTIMIZE/RE_DO 固定受影响阶段及其后继，对需要执行的阶段创建新 StageRollout、新会话与新轮次，保留旧事实。图示按 name 表达的 adjusted_stages YAML 只允许引用当前配置中的唯一名称；instruction 不得改变候选/依赖或验收合同。
5. 已成功且不受影响的阶段可通过内部证据映射复用；不能把旧 session_id 冒充新阶段会话，也不能以复用为理由重写旧产物/结果。旧在途分配撤销，迟到上报被拒绝。
6. SHUTDOWN 经明确权限停止继续调度，并记录取消意图；远程取消只有实际确认才宣称成功。图中六个状态没有 STOPPED/CANCELLED，首版用内部停止事实阻断调度并保留最后业务状态，不把停止伪装为 RUN_FAILED，也不私自扩充枚举；后续如需公开停止状态，单独演进合同。
7. 原有 FAILED Plan 不能重规划的限制须改造为“经授权可创建后继轮次、旧失败历史不变”；不能直接声称调用现有 build_revision 已经支持。
8. UUID 回执、结果、状态/后继身份和 outbox 意图同事务提交；提交失败不消耗 UUID。内部使用本次读取的 revision 做 CAS，和进度上报、其他干预竞争；不自动重算/重试旧建议。重复调用输掉 CAS 时从新事务查询对应回执，存在则返回原结果，否则保留冲突。并发重复可以重复计算无副作用的建议，但只能提交一次干预效果；该能力不得自行持久化或调用派发/取消。服务重建后仍从 SQL 重放，不依赖进程内缓存。

会话摘要可作为 interactive 的内部辅助，不能替代用户要求的消息明细；不新增公开 get_summary 方法。修改 Profile 的 DAG 结构或验收条件应新建 Profile/新协作请求，不用干预参数绕过配置管理。

### 5.7 query_stage_rollout_details：按 session_id 查询会话消息明细

**主要职责是读取该阶段真实 session 的会话消息，不是重复查询 Stage 状态，也不是返回 Event-backed conversation 或摘要。**

```text
stage_rollout_id
  → 查 StageRollout 及内部所属/执行者/运行时路由记录
  → 校验调用者对该阶段/会话的读取权限
  → 取 StageRollout.session_id
  → 使用实际会话 provider 的消息读取 Protocol
  → 返回该 session 的消息明细
```

- 业务入口只收 stage_rollout_id，服务端加载 session_id；不让调用者传一个任意 session_id 绕过阶段授权。
- 待实施 `StageRolloutDetails` 响应至少包含 `stage_rollout_id`、`session_id`、`messages`；SessionMessage 由 adapter 保留消息 ID、角色、消息内容及提供者实际支持的时间/工具调用信息。不能只返回截断摘要或占位文本声称是消息详情。
- 会话尚未创建（session_id 为空）时返回空 messages 并在响应元数据明确未绑定；已绑定且真实零消息也是空列表，但应标明查询成功。不存在阶段报 not found；禁止越权时不调用 provider。
- provider 超时、不支持或会话不存在时明确返回对应领域错误；不静默退回 SQL Event 列表，不把上游失败伪装成“没有消息”。
- 优先复用现有 `OpenApiBotPort.get_session_messages(session_id, limit, bot_id, lifecycle_stage)` 或 `BcsClientPort.get_session_messages`，由 adapter 按真实派发链选择。bot/owner/lifecycle_stage/provider 从可信执行记录取，不能靠 UUID 字符串猜测。
- 现有 Bot port 只有 limit、没有通用 cursor 合同。首版传递有界 limit，响应标明范围/顺序及完整性未知时的限制；不得宣称已返回全历史或跨 provider 精确分页。扩展能力时同步改 port 与消费者测试。
- 查询不发消息、不创建会话、不推进阶段状态，也不因为上游支持就泄露内部凭据、系统机密或其他 Stage 的会话。

## 6. 持久化、旧模型迁移与契约替换

### 6.1 新权威与复用边界

ProductProfile、Collet、ColletRollout、StageRollout 是新合同中的正式可持久化对象；Artifact/AcceptanceResult 可作为阶段及事件的嵌套值保存，不要求每个值单独建表。ProgressEvent 接纳以追加事件/回执保存。

仓储还需保存作用域、创建者/owner、业务对象 revision、当前轮次指针、轮次父链、阶段依赖、实际分配/尝试、会话路由、Artifact 资源映射等技术元数据。它们不等于给领域类偷偷增加成员，也不能把安全前提只留在进程内。

首版建议显式存储 Profile/Collet/轮次/阶段及操作回执/投递意图；复用现有表或新增表的 DDL 在实施前固定。创建/报告/干预用一个事务或等价可证明的提交单元维护对象、G4 事实及回执；网络 IO/模型调用在提交之外，以固定幂等键对账。不承诺无远端支持的 exactly-once。

若保留现有 L4 Task、L5 Plan/Stage/Allocation，必须先定义适配关系及单一写入口：它们承接底层执行/Work，不再是对外产品定义的第二权威。新对象不能一份在 SQL、对应旧对象一份在内存任意变更；持久化完整治理/参与前提，损坏或缺失 fail closed。

### 6.2 必须一起迁移的地方

| 当前对象/边界 | 目标与处理 |
| --- | --- |
| `ProductProfileDefinition`、codec、catalog、reader/publication、所有消费者 | 改为新 ProductProfile，保留 composition 并新增 stages YAML 合同；codec/reader 对二者完整往返。最终删除旧类型，不长期保留 alias；旧发布授权收敛到新 create 的配置管理权限。 |
| `profiles.py` 的同名枚举 | 重命名为兼容 key，清理新域的枚举成员引用，旧 wire 值只留在 adapter。 |
| 旧字符串 Profile ID + version | 用显式映射分配新整数 id；原版本不可被同名最新配置覆盖。保留可表达的 composition；其 workflow DAG 提取/适配须显式校验与 stages 一致。无法表达的组合或 governance 不能丢弃后冒称无损迁移。 |
| 原 Collet 字段及其独立生命周期 | 正式改为图示字段；scope/owner/版本/资源/事件等仍由内部记录和窄合同保存。状态机、仓储、HTTP DTO 和消费方同步调整。 |
| 原 Plan/Stage 查询视图 | 转为正式 Rollout 对象的持久化/适配读取；保持新 ID、父链与当前轮次明确。 |
| 旧 Artifact(ResourceBinding) | 显式解码为 File/Link/Text 或生成受控转换记录；缺少内容/类型信息时标记不可迁移，不捏造 File/Text。新域不保留相同名字的两种 shape。 |
| 旧成功 StageExecutionReport | 兼容适配成新 ProgressEvent 语义并重新验证；没有验收结果不能凭空填 passed=true。 |
| 旧 TaskCommission / Swarm HTTP/DI | 迁移到新的 ProductProfileServiceProtocol；旧路由如保留只能是有期限兼容 adapter，不继续作为新能力唯一入口。 |

这是对现有契约的实质变更，不是全部向后兼容的小重构。实施前补模型/状态/存储迁移决策，更新 owning contracts、Context Boundary、消费者测试；涉及已接受 ADR 时先提出显式替代。当前文档不伪称替代已经获准或落地。

切换优先排空旧进程内在途执行，再导入可验证的历史数据；不能只凭 SQL child Task 重建丢失的 Method/会话/治理历史。新权威写入后禁止旧 writer 混写；回滚需停写及验证兼容 reader，不能覆盖新事实。

## 7. 验收与本次交付范围

最低旅程：创建同时包含 MethodCompositionTemplate 与图示 stages YAML 的 ProductProfile → 创建 Collet/初轮/阶段 → 真实分配并保存 session_id → 按 stage_rollout_id 查询真实消息 → 接纳验收失败 → interactive 创建后继轮次 → 查询全部轮次 → 拒绝旧执行迟到报告 → 新轮通过并按产品合同收敛整体状态。

必须验证领域对象的**精确字段集合**、composition 与 YAML 的完整往返/联合校验、optional methods 不隐式启用、重复 DAG 配置冲突拒绝、YAML 单条件/多条件、三种 ArtifactContent 往返、ProgressEvent 三种类型、五个服务方法的精简签名、真实消息 provider 调用与越权无调用，以及独立创建、干预 UUID 的顺序/并发/服务重建重放、同 UUID 改内容或 rollout 冲突、内部 CAS、报告幂等和提交失败。身份作用域须覆盖缺失拒绝、异常恢复及并发隔离；HTTP 不要求旧请求字段且拒绝未知字段。

**原方案阶段交付记录（历史）**：当时仅修改 spec/plan/research 和导航，没有实现新对象、模块、方法、DDL 或接口；48 passed 仅为旧实现基线。原文档检查见 [research.md](research.md)。

**开发阶段结果**：新的正式领域对象、五方法 Service API、SQL 单一轮次权威、真实会话端口适配及独立签名 Bot 进度入口已落地；旧产品兼容及外部验证边界没有被隐去。请阅读 [开发与验收交付记录](delivery.md) 及 [当前模块合同](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)。
