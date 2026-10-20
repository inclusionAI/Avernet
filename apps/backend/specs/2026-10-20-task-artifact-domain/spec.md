# task 模块产物领域对象(Artifact Domain)设计

- 日期:2026-10-20
- 状态:DRAFT(方案评审中,未实现)
- 参与模块:`core/task`(domain / task_context / task_runner / repository)、`core/repository`(protocols/implementations)、`adapters/http/task`
- 本地代码:branch `dev_task_cli_1015`,基线 `b6d6b12f7`
- 输入:语雀《产物领域对象》(yuque `mad/enxdbg/gfap3r7tgc2aeptr`,2026-09-17 建议稿)作为设计参考;仓库现状代码勘察(2026-10-20)
- 约束:遵循 `AGENTS.md` / `CONTEXT-MAP.md` / `docs/arch`(Rule 22:HTTP adapter 只转协议;core/repository 四条注册纪律)
- 关联决定:本方案为**全新设计**,不以任何历史分支上的实现为前提
- 验证状态:未验证(仅设计;实现后补充测试证据)

> 本文所有标注 **(方案取舍)** 的条目是设计者在本次讨论中自行拍板的实现取舍,用户未逐条确认;有异议请直接推翻。

---

## 1. 问题与目标

### 1.1 现状

task 模块中节点"产物"的现状是 `RuntimeInfo.output: dict[str, Any]`(`core/task/domain/models.py:316`),三个事实构成问题:

1. **文本产物无身份**:bot 结果以 `result` / `markdown` / `output` 等**纯约定键**进入 dict(写入点:pull 通道 `callback_adapter.py:284/:315/:336`、push 通道、BBS `execution_adapters.py:712`、relay `relay_report.py:250/:308`),没有业务身份、内容类型、重试归属,`_relay_output_value`(`task_graph_support.py:184`)靠"单键字符串"约定做投影;
2. **文件产物无处安放**:bot 在容器里交付的文件,单 bot 会话落在 bot 工作区 resource 文件(路径寻址,读口 `GET /api/resources/files/preview`)、协作群已被 bcs-cli 上传进群聊 session file(file_id 寻址,读口 `GET /sessions/{sid}/files/{fid}/content`,`BcsSessionFilesClient`),但回调协议里**没有任何文件字段**——文件信息只能作为自由文本混在 output 字符串里,出容器即失义,下游/看板/审计解析不到字节;
3. **重试即覆盖**:节点重试时 `output.clear()`(`task_graph_service.py:723`),上一 attempt 的产出无迹可查。

### 1.2 目标

引入独立的 **TaskArtifact(不可变产物清单)** 领域对象,统一记录"协作过程中产出了什么":

- **G1 文件产物有结构化记录**:回调上报的文件产物(来源 + session_id + 文件名 + 路径或 file_id)成为一等公民,后续人下载(前端拼链接)、下游消费、审计回溯都拿稳定引用;
- **G2 文本产物统一纳管**:文本继续留在 output dict 兼容现有实现,同时记成 Text 产物,与文件产物走同一套身份/血缘/审计;
- **G3 不可变与重试隔离**:产物发布后不可变;重试产生新归属(按 attempt),不覆盖旧记录;
- **G4 单事务一致性**:产物落库与驱动它的图变更是同一持久化事务,失败上抛、不吞。

### 1.3 非目标(本期不做)

- **不搬字节**:文件字节不进 Artifact、不进图表、不进 Prompt;后端不新建 preview/下载/presign 接口——二进制下载链接由前端基于既有读口自行拼接;
- **不切读侧消费路径**:下游节点拼 Prompt、验收/Judge、OpenAPI 任务导出等消费方**继续读 output dict**,不改;
- 不引入 `Structured` / `Collection` 分支、`supersedes` 内容修订链、Schema Registry(语雀文档后续演进项,待真实需求驱动);
- 不改造 SessionFile / 附件 / 存储分层(BCS 已管好字节生命周期);
- 不动 `RuntimeInfo.output` 的现有语义与所有现有写入方。

### 1.4 反目标(做了即算失败)

- Artifact 里出现临时下载 URL、token、对象存储 handle 或后端实现细节;
- 为记录产物而破坏现有回调/图变更的幂等与版本重放语义;
- output dict 与产物对象出现"双首领"(产物写失败但图写成功且不报错,或反之);

---

## 2. 现状写入链路(设计锚点)

产物发布的挂接点必须落在真实写入链路上,以下为勘察结论(2026-10-20,行号基于基线 `b6d6b12f7`):

| 通道 | 入口 | output 写入点 | 文件信息现状 |
|---|---|---|---|
| 集中 pull(单 bot/群) | poller `task_executor_result_poller.py:86` → `CallbackAdapter._adapt_poller` | `callback_adapter.py:284/:315` → `update_task_node_info` fold(`task_graph_service.py:658`) | 无字段;仅自由文本 |
| 技能 HTTP push | `callback_routes.py:47` 前缀路由 | `callback_adapter.py:336` → 同上 fold | body schema 无文件字段 |
| relay 两事件 | `schemas_relay_report.py:168` → `relay_report.py:140` | `relay_report.py:250`(ACCEPTED)/`:308`(completion,同次原子变更) | `extra=forbid`,无文件字段 |
| BBS | `bbs_routes.py:44` → `on_bbs_report` | `execution_adapters.py:712` + `bbs_modal_executor.py:570` | 无字段 |

所有通道最终收敛到 **单一版本化网关**:`_mutate_with_version_retry`(`task_graph_service.py:354`,`MAX_GRAPH_VERSION_RETRIES=3`,变更必须纯/可重放)→ `_persist_locked`(`:199`,一事务内 `save_graph(graph, expected_version, action_events, callback_audit)`;callback audit 经 contextvar `_PENDING_CALLBACK_AUDIT` 暂存后在同一事务消费——这是本方案复用的机制)。

**attempt 口径**:`RuntimeInfo.extend_props["harness_retries"]`(声明于 `domain/models.py:490`),集中通道增量在 `centralized_recovery.py:307`,relay 通道在 `task_recovery.py:181`;重试增量会清空上一 attempt 的 output(`task_graph_service.py:721-724`)——因此产物**必须**在 fold 之后的快照上取值并按 attempt 归档,才能保住重试前的记录。

**读口(产物引用的解析终点,全部已存在)**:

- 单 bot 工作区:`GET /api/resources/files/preview`(文本预览,1MB 上限;`bot_resource_file_cli.py` 为其 CLI 验证件);
- 群聊 session file:`GET /sessions/{sid}/files`(列)与 `GET /sessions/{sid}/files/{fid}/content`(下载;local 直流 / baas 302→presigned),参与者级 ACL,`BcsSessionFilesClient`。

---

## 3. 领域模型

### 3.1 TaskArtifact(聚合根,不可变)

```python
@dataclass(frozen=True)
class TaskArtifact:
    artifact_id: str            # "task-art-" + uuid4().hex[:16]
    artifact_name: str | None   # 业务展示名(非物理文件名);上报可选,None=未提供(见 3.6)
    kind: ArtifactKind          # 内容形态:text / file(见 3.2)
    scope: ArtifactScope        # 归属(见 3.3)
    content: ArtifactContent    # 互斥内容表示(tagged union,见 3.4)
    content_hash: str           # sha256(规范序列化 content),幂等去重键成分
    creator: str                # 产出者 id:取发布时点节点 RuntimeInfo.assignee(bot_id/group_id);系统写入(控制信号等无 assignee)记 "system";落表列名 creator
    created_at: int             # 毫秒;落库由仓储折叠进 gmt_create(trajectory 同款先例),不占独立 bigint 列
```

不可变性:发布后不提供任何原地修改入口;内容修订(未来的 `supersedes`)是新增 artifact_id,不是更新。

### 3.6 artifact_name ——(方案取舍)业务展示名槽位

- **语义**:业务展示名,与 `FileContent.file_name`(物理文件名,参与寻址与校验)刻意分离——两者会分叉(文件 `output-1734393001.md` 的业务名可以是"数据分析报告");文本产物借此获得看板可渲染的标题,不再只剩截断文本;
- **来源与默认**:上报可选(§4 artifacts 块 item 加可选 `artifact_name` 字段);FILE 产物未上报时回落 `file_name`,TEXT 双轨产物与 URL_LINK/CODE 产物无回落来源、默认 `None`,前端对 None 自行回落渲染(TEXT 截断文本,URL_LINK 用 url,CODE 用 pr_link);
- **诚实边界**:协议让 bot 上报业务名之前,文本产物该字段实际全为 `None`、文件产物多为 `file_name` 复制——买的是槽位与统一渲染口径,不是当下就有差异化数据;不进幂等键、不建索引,进 §7 Descriptor。

### 3.2 ArtifactKind ——(用户定稿)内容形态枚举:text / file / url_link / code

`ArtifactKind` 的取值定义为**内容形态**,与 `ArtifactContent` 分支一一对应:

```python
class ArtifactKind(StrEnum):
    TEXT = "text"          # 文本产物(TextContent)
    FILE = "file"          # 文件产物(FileContent)
    URL_LINK = "url_link"  # 链接产物(UrlLinkContent)
    CODE = "code"          # 代码产物(CodeContent)
```

- kind 与内容分支类一一对应(`TEXT↔TextContent`、`FILE↔FileContent`、`URL_LINK↔UrlLinkContent`、`CODE↔CodeContent`),是落表(`kind` 列)与查询过滤用的一等枚举;**加 kind 值 = 引入新内容分支**(如 `STRUCTURED`/`COLLECTION`,演进阶段三),未知 kind 解析 **fail-loud**,不静默丢弃;
- 语雀文档原方案的"业务用途 kind"(analysis_report/report/dataset…)不在本期引入:现有回调协议里没有任何业务分类信号,开放字符串会立刻退化为无含义的自由文本;待回调协议具备上报业务分类的字段后,以受治扩展(枚举加值或独立业务分类字段)再议,不预埋;
- **(方案取舍)控制信号归入 text**(不再单列枚举值):`skipped`/`notify_result` 这类信号(写入点 `dispatcher.py:608/:761`)是审计链的一部分,照常记录为 `TEXT` 产物——`text = canonical_json(整个 output dict)`(见 5.1);文本分支不携带格式元数据,"是控制信号还是正文文本"由内容本身可辨,代价是形态信息不进结构化字段。

### 3.3 ArtifactScope

```python
@dataclass(frozen=True)
class ArtifactScope:
    session_id: str | None   # 图绑定的协作会话(建图时写入根节点 extend_props["session_id"])    
    task_id: str
    node_id: str
    attempt: int             # = 发布时点该节点 extend_props["harness_retries"] 快照(0 起)
```

- attempt:双轨派生产物取 fold 之后的节点快照值(同批 harness_retries 立即生效);上报产物(relay)的 attempt 来自报文 item 但**与图上快照交叉校验一致**(§4),口径统一为 `NodeActionEvent.attempt`、relay 幂等键 `f"{node_id}:EXECUTION_RESULT:{attempt}"`(`relay_report.py:173`);
- `session_id`:图绑定优先——取根节点 `run_info.extend_props["session_id"]`(建图时由 `task_info.execution_config["main_session_id"]` 写入,`task_graph_support.py:106-110`);图未绑定会话时采纳**报文 item 的 session_id**(§4 校验规则 5);"任务跑在哪个协作会话里"的归属事实。注意与 `FileContent.storage_id`(文件存储寻址)语义不同;
- **(方案取舍)不做图级产物**:relay 的 `graph.output = _relay_graph_output(graph)`(`relay_report.py:310`)是聚合投影,保持现状不建 artifact;图级"最终输出即产物"的语义留到读侧切换阶段再议。

### 3.4 ArtifactContent —— tagged union,四个形态分支

```python
@dataclass(frozen=True)
class TextContent(ArtifactContent):
    text: str   # 纯文本内容,不带格式元数据(markdown/plain/json 不做标注)

@dataclass(frozen=True)
class FileContent(ArtifactContent):
    storage_type: StorageType    # BOT_WORKSPACE | SESSION_FILE
    storage_id: str              # 统一存储寻址:BOT_WORKSPACE=工作区路径;SESSION_FILE=文件所在会话 id
    file_type: str               # 缺省按 file_name 扩展名推断,推不出为 application/octet-stream
    file_name: str
    file_id: str | None          # SESSION_FILE 必填;BOT_WORKSPACE 为 None

@dataclass(frozen=True)
class UrlLinkContent(ArtifactContent):
    url: str        # 稳定业务链接,http(s)
    type: str       # 链接业务类型(开放字符串:dima需求 / 语雀文档 / …)

@dataclass(frozen=True)
class CodeContent(ArtifactContent):
    code_base_name: str      # 代码库名
    code_base_address: str   # 代码库地址(http(s))
    pr_link: str             # PR 链接(http(s))
    pr_desc: str             # PR 描述(允许空串)
```

内容分支类**不带 `kind` 字段**——形态的单一事实源是 `TaskArtifact.kind`(3.1)与分支类本身;tagged union 只存在于**序列化形**:content 落库为 `{"kind": "text"|"file"|"url_link"|"code", ...}` 的 tag 前缀 JSON,tag 由序列化器负责——写入时按分支类派发(isinstance),读取时按 tag 选构造类。这样领域模型无重复字段,type 也不靠 `Literal["text"]` 兜护。

- 未知 `kind` tag → `TaskArtifactContentError`(domain errors),不猜、不降级;
- **`storage_id` 是唯一的存储寻址字段**:BOT_WORKSPACE → 工作区相对路径值;SESSION_FILE → 文件所在会话 id 值。上报方在 `file_content` JSON 里**直接提供** `storage_id`(按此语义填值),后端不再做 path/session_id → storage_id 的映射归一。读侧/前端拿 `(storage_type, storage_id[, file_id])` 即得完整寻址:BOT_WORKSPACE 走 `GET /api/resources/files/preview?path={storage_id}`、SESSION_FILE 走 `GET /sessions/{storage_id}/files/{file_id}/content`;
- `FileContent` 互斥性在领域构造器校验:`BOT_WORKSPACE` → `file_id` 必须为 None、`storage_id` 做相对路径校验(拒绝绝对路径与 `..` 逃逸,长度上限 512);`SESSION_FILE` → `file_id` 必填,`file_id`/`file_name`/`storage_id` 长度与字符校验;
- `UrlLinkContent`/`CodeContent` 校验:`url`/`code_base_address`/`pr_link` 必须是 http(s) 前缀且长度上限 2048;`type` 为开放字符串(不建受治枚举,业务类型词汇由调用方生态演进;非空,长度上限 64);`code_base_name` 非空;`pr_desc` 允许空串、长度上限 8KB;
- **信任边界(方案取舍,已显式声明)**:回调上报的引用**只做结构校验,不做归属交叉核验**——上报人(已鉴权的回调发送方)可信,`storage_id`/`file_id` 原样进产物与读侧 Descriptor。风险敞口 = 看板读者可见到上报者提供的会话标识与文件元数据;**字节不因此泄露**(两处读口均为参与者级 ACL)。有限归属核验(如"上报 session 须与节点 assignee/relay 会话相关")列为后续加固项,本期不建;
- 字段里**不出现**:**文件字节的临时访问凭证**——presign、带 token 的下载 URL、object handle、宿主机绝对路径;`file_type` 是引用元数据(供前端挑下载/预览行为),不是字节承诺;文本分支连格式元数据也不带,text 即全部内容。**边界澄清(方案取舍)**:`UrlLinkContent.url` 与 `CodeContent.pr_link`/`code_base_address` 是**产物内容本身**——bot 交付的稳定业务链接(需求条目、语雀文档、PR),不是"文件字节怎么取"的临时能力,不受上述禁令约束,原样入库入 Descriptor。

### 3.5 血缘 ——(方案取舍)本期只到"作用域 + 创建者"

语雀文档的 `derived_from / source_message_ids` 在现有回调信号里同样没有来源,取舍:血缘信息隐含在 `scope`(run/node/attempt 归属)与 `creator`(谁产出)里;派生关系(下游产物 ← 上游产物/消息)等回调或编排层能真实表达依赖时再加,不预埋自由 JSON。

---

## 4. 回调上报契约(产物怎么进来)

产物信息的上报**只走接力链路**(用户定稿):`POST /api/v1/collaboration/tasks/callback/report`(`adapters/http/task/router.py:463`,两事件合约落点 `:559`),在 `ExecutionEventDTO`(`schemas_relay_report.py`,`extra=forbid`)的**顶层**新增 optional `artifacts`(产物列表,与 `trace` 同级)。列表 item 为**扁平自描述结构**(用户定稿):每个 item 携带产物名、kind、归属字段和**恰好一个**内容字段,四个内容字段互斥、各自承载对应分支的 JSON **字符串**:

```json
{
  "artifacts": [
    {
      "artifact_name": "分析报告",          // 可选(见 3.6);file 产物缺省回落 file_content.file_name
      "artifact_kind": "file",             // text / file / url_link / code,须与非空 content 字段一致
      "session_id": "sess-xxx",            // 图绑定的协作会话,与 3.3 scope.session_id 交叉校验
      "task_id": "t-a1b2",                  // 与报文级 task_id 交叉校验
      "node_id": "relay-ab12cd34",           // 与报文级 node_id 交叉校验
      "attempt": 1,                          // 与图上 harness_retries 快照交叉校验
      "text_content": null,                 // text 分支 JSON 字符串,如 "{\"text\": \"...\"}"
      "file_content": "{\"storage_type\":\"session_file\",\"storage_id\":\"sess-xxx\",\"file_type\":\"application/pdf\",\"file_name\":\"analysis-report.pdf\",\"file_id\":\"f-987\"}",
      "url_link_content": null,             // url_link 分支 JSON 字符串,如 "{\"url\":\"https://...\",\"type\":\"dima需求\"}"
      "code_content": null                  // code 分支 JSON 字符串,如 "{\"code_base_name\":\"avernet\",...}"
    }
  ]
}
```

| 通道 | 行为 |
|---|---|
| **relay(/callback/report)** | `ExecutionEventDTO` 顶层新增 optional `artifacts`,item 为**扁平自描述结构**(见上 JSON):`artifact_kind` + 恰好一个 content 字段(互斥),`task_id`/`node_id`/`session_id`/`attempt` 与报文/图上真值交叉校验;`to_domain` 转 `TaskNodePatch.artifact_patch`;只有 `execution_decision=ACCEPTED` 的事件允许携带,**DECLINED 携带即非法**;产物与 `output_patch` 同批生效(写入同一原子变更,见 5.1 relay 发布点) |
| 集中 pull / 技能 HTTP push | **协议不动**:报文不加 artifacts 字段,`artifact_patch` 恒为 None;文本双轨产物照常经由 `update_task_node_info` fold 生效 |
| BBS | 协议不动(其 output 为 BBS 编排文本),文本双轨照常生效 |

- `TaskNodePatch` 新增 `artifact_patch: list[ArtifactReportItem] | None = None`(None=未上报,[] = 明确空);**列表长度上限 64(方案取舍)**,超限整批拒绝;
- **item 校验规则**(任一不满足 → 整个回调按 `exec_error` 处理进既有失败路径,**不部分接受**——产物记录宁可没有,不可有假):
  1. 四个 content 字段(`text_content`/`file_content`/`url_link_content`/`code_content`)**恰好一个**非空,其余为 null;`artifact_kind` 与非空字段的分支一致(text/file/url_link/code);
  2. content 字段值为合法 JSON 字符串,反序列化走 §3.4 既有分支构造器校验(unknown tag 字段名/互斥破坏/路径逃逸/非 http(s) 链接等);content JSON **不含 kind tag**——判别信息由字段名承载;
  3. `task_id`/`node_id` **必须等于本次报文级定位**(callback 本就寻址唯一 node),不一致即非法;
  4. `attempt`(int ≥ 0)**与图上 `harness_retries` 快照一致**(与 relay 既有 attempt 精确校验口径同源),不一致即非法——防止幂等键被误导(方案取舍:拒绝而非采纳,诚实优先);
  5. `session_id` 与图绑定会话交叉校验:图已绑定时必须一致;图未绑定时采纳上报值进 scope.session_id(信任已鉴权回调方,与 §3.4 信任边界一致)——scope 不再"图中无会话则伪造 None"单一语义,改为"图优先、上报兜底"。
- `text_content` 与双轨派生并存:TEXT 产物有两个来源(bot 显式上报 + 后端从 fold 后 output 派生),同 scope 同内容由幂等键收敛为一行,互不重复计账。
- **(方案取舍)产物上报收敛在 relay 一条口**:接力链路是当前基线上 bot 执行回调的主协议,产物字段只在此扩展(`extra=forbid` 的 DTO 加可选字段是兼容变更);集中 push/pull 的报文本动不动,后续若有需要按同构 item 字段平移,不在本期预铺;`artifacts` 位于报文顶层而非 `output_patch` 深处——产物是回调契约里可枚举、可校验的一段,不稀释 output dict 的既有语义。

---

## 5. 写侧挂接:发布点、事务、幂等

### 5.1 发布点(capture)

发布动作发生在**图变更闭包内部、fold 之后**,从提交前快照取值,保证版本重放安全:

1. **集中通道**:`update_task_node_info` 中 `node.run_info.output.update(patch.output_patch)`(`task_graph_service.py:658`)及 graph 级 `update_task_graph_info`(`:814`)之后,基于 fold 后 output 快照构造 artifact 集(本期集中通道 `patch.artifact_patch` 恒 None——只有 relay 通道携带产物列表,见 §4;集中通道只发文本双轨产物);
2. **relay 通道**:`apply_relay_report` 的两处写入(`relay_report.py:250` ACCEPTED EXECUTION_RESULT、`:308` completion)之后,在同一 `_mutate_with_version_retry` 闭包内构造;
3. 文本产物(双轨)在发布点由 fold 后 output 派生,归约器**独立新增**(capture 语义,不复用也不改动 `_relay_output_value`):
   - `_artifact_text_value(output)`:output 为**单键** `result` / `markdown` / `output` 且值为非空字符串 → 该字符串(必须含 `output` 键:集中 pull/push 与 BBS 三大主通道的文本都以 `{"output": <str>}` 落库,不纳入则双轨形同虚设);产出 `TEXT` 产物(内容原样入 `TextContent.text`,不标注 markdown/plain 格式),每 attempt 至多一条;
   - 与 relay 投影是**超集且互不影响**:relay 的 `graph.output` 聚合口径、`_relay_output_value`(`task_graph_support.py:184-207`)原地不动,可能出现"节点有文本产物、relay 聚合仍投影为 dict"的合法分叉,这是双轨设计的预期形态而非缺陷;
   - 归约不出字符串的 dict 形态 output:**含控制信号键**(`skipped` / `notify_result`,写入点 `task_dispatch/dispatcher.py:608/:761`)→ 记一条 `TEXT` 产物(`TextContent(text=canonical_json(整个 output dict))`),每 attempt 至多一条;其余(真实业务数据 dict)本期**不记**,等 Structured 分支——这是明确的有界决策。

### 5.2 事务与失败语义

复用 callback-audit 的 **contextvar 暂存 + 同事务消费**机制(callback audit 的完整先例:contextvar 定义 `task_graph_service.py:103-128`、暂存点、`_persist_locked` 内随图写一并消费,graph 仓储实现内嵌 upsert):

- 发布闭包把待写 `TaskArtifactRecord` 暂存到 `_PENDING_PUBLISHED_ARTIFACTS`(闭包语义,与 replay 对齐:版本冲突重放会重复暂存,由幂等键吸收);
- **落表通道(方案取舍)**:`save_graph` 的事务在 `TaskGraphRepository` 内部开启且不外泄 session(`task_graph_repository.py:245` `with self._transaction()`),因此产物行**作为 `save_graph` 的新增可选参数 `published_artifacts` 随图一并提交**,由图仓储在自己的事务里内嵌写入——先例:`save_graph` 已经在自家事务里内嵌写 `TaskCallbackModel` 行(尽管存在独立的 `task_callback_repository.py`)。`TaskArtifactRepositoryProtocol` 只承担**读侧查询**(按 task/node 列产物),不承担事务内写;
- 异常语义:产物写入异常 → 整个事务回滚、图变更一并失败、上抛——**不吞、不降 WARNING**(语雀文档不变量:持久化失败不得返回成功);
- **graph 仓储缺席态**(内存-only 部署,`_persist_locked` 提前返回):不发布(contextvar 暂存随闭包丢弃,记一条 debug 日志)——与取舍 #10"未接线不发布"同语义;
- 版本重放扇出(同一次逻辑产出在重放/重试回调里重复来到)由唯一幂等键吸收(5.3)。

**(方案取舍)不采用"提交后异步补写"**(relay trace rows 的 post-commit 模式,`task_service_relay.py:96`):那是有意 best-effort 的旁路;产物是审计对象,选择强一致、宁可让变更重放。

### 5.3 幂等键

- 表级唯一键 `uk_dedupe(task_id, node_id, attempt, kind, content_hash)`,`content_hash = sha256(canonical_json(content))`;
- **写入策略(方案取舍)select-first、唯一键兜底,不做事务内"冲突后回滚再读"**:图仓储写产物前先按 `uk_dedupe` 键 SELECT(`save_graph` 已对 `task_info` 行 `with_for_update` 预锁,同任务写入串行,SELECT-first 之后竞态几乎不可达);仍撞唯一键则**让本事务失败走版本重放**,下一轮 SELECT-first 命中即幂等返回。不采用"IntegrityError → rollback → 回读"配方的原因:部分后端(PG)约束冲突后事务即废;而本代码库 prod plugin 的 `orm_session()` 不支持 `begin_nested` SAVEPOINT(skill_center/skill.py:2551-2558 已记录),事务内回滚读在该 profile 上不可实现。此口径的验收针对 SQLite(singlebox/测试)与 MySQL/OceanBase(prod)两个 profile 定义;
- 到达层已有的事件级幂等(callback `event_id` PROCESSED 门、relay receipt/fingerprint)不变,产物级幂等键是最后一道闸。

### 5.4 重试与覆盖

- 产物发布只增不改:重试增量(harness_retries+1 → output.clear())不影响已落库的上一 attempt 产物——**这正是引入 artifact 后新获得的能力**(现状:上一次产物直接消失);
- 同 attempt 内同一内容重复上报(回调重放)命中幂等键;同 attempt 内容变化产生新 content_hash,两条并存(本期没有"哪个算最新"的选主问题:读侧未切换,选主问题留到读侧切换阶段再议)。

---

## 6. 持久化

按 core/repository 四条纪律落地(README.md 治理,`protocols/task/` 与 `implementations/task/` 镜像)。

**为什么独立建表、不复用 `task_node_run_info`(已评估的取舍)**:该表是"可变当前态"行(`gmt_modified onupdate` 原地更新,每次 `save_graph` 随图整体重写,uk 为 `(task_id, node_id, retry)`,见 `repository/models.py:199-228`),而产物是 append-only 不可变清单;一次 attempt 的产物是列表(≤64),并入单列只能是 blob——逐条身份与 `uk_dedupe` 无处落,回放去重退化为整 blob 比较;且 run_info 行已被 `output`/`acceptance_result`/`extend_props` 占用,再并产物会把"每次图保存重写全节点行"的写放大带给产物。独立表 + `save_graph` 同事务内嵌写,一致性机制不变,只多一次性的建表/注册劳动。

- **SQL**:`core/task/sql/2026_10_20_task_artifact.sql`(权威 DDL 如下,plan 阶段照此落盘;标识符列的 utf8mb4_bin 由 ORM variant 承载,与 trajectory 同款先例——DDL 裸列、ORM 定义 collation);
- **ORM/Record**:`TaskArtifactModel`(`core/task/repository/models.py`,house style:`to_record()` 返回 frozen record,不吐 ORM 对象)+ `TaskArtifactRecord`(`types.py`,table-faithful frozen dataclass,content 存解析后的领域对象;`storage` 列为派生一致性列——非 file 分支持 `inline`、file 分支与 `FileContent.storage_type` 同值,domain 构造器派生强校验,ORM 侧照表存列);
- **协议(方案取舍:读侧协议,不承担事务内写)**:写行由图仓储随 `save_graph(published_artifacts=...)` 内嵌完成(5.2,先例为 `TaskCallbackModel`);`TaskArtifactRepositoryProtocol` 只定义读契约(按 task_id / (task_id, node_id) 列产物,attempt 倒序),入 `core/repository/protocols/task/`(纯 Protocol、`@abstractmethod`、无运行时域导入),实现在 `implementations/task/task_artifact_repository.py`,共享 `orm_session()`/`flush()` 习惯;`TaskGraphRepositoryProtocol.save_graph` 签名变更(新增可选参,默认 None=行为不变)——所有现存实现与测试替身在该 commit 内同步;
- **注册**:`core/repository/README.md` provides 表 + Boundary YAML 增补;`di/modules/task_persistence_module.py` `configure` 绑定 singleton;
- **domain**:`core/task/domain/errors.py` 加 `TaskArtifactContentError`;`TaskArtifactPublishService`(capture 纯函数 + contextvar 暂存)挂 `task_context` 旁,`TaskModule` 经 `_resolve_optional_port` 同款 try/except 解析(未绑定 → 不发布,只_warning 一条,与 correlation registry 口径一致——注意:这是"可选端口未接线"的启动期语义,不是运行期持久化失败的降级)。

**`task_artifact` DDL**(`2026_10_20_task_artifact.sql`):

```sql
-- task_artifact: 产物领域对象(TaskArtifact)的持久化,本 spec §3 领域模型的表形态。
-- append-only 不可变清单:仓储不提供任何 UPDATE 路径;内容修订(未来 supersedes)以新行表达。
-- 写入由 save_graph 事务内嵌完成(同 TaskCallbackModel 先例);读侧经 TaskArtifactRepositoryProtocol。
-- 无外键(与 task_trajectory 同款约束:task 域表间不建 FK)。
-- 幂等二道闸:select-first 后 INSERT,撞 uk_dedupe 让本事务失败走图版本重放(spec §5.3),
-- 不做"冲突回滚再读"(prod plugin 无 SAVEPOINT)。
-- 索引语义(不带索引级 COMMENT——内部 SQL 审核解析器不识别,且 trajectory 同款无索引注释先例;
-- 语义收拢于此):uk_artifact_id=产物全局唯一 ID 闸;uk_dedupe=回放去重幂等键,最左前缀同时
-- 服务 task_id / (task_id,node_id) 检索;idx_scope_session=按会话列产物+时间序;
-- idx_task_kind=任务内按形态过滤+时间序;idx_task_name=任务内按业务名检索
-- (LIKE 'x%' 前缀走索引,'%x%' 中缀不走)。
-- 可空性统一以裸 NULL 声明(无 DEFAULT NULL,用户定稿):content 为 TEXT 家族禁显式 DEFAULT,
-- 其余可空列与隐式行为等价,统一写法消除混排。
-- 除 artifact_name/scope_session_id/storage(真可空:未提供语义)外,node_id/attempt/
-- content_hash/creator/modifier 为"列可空、发布必填":NULL 仅为 schema 容错/人工修数留口;
-- task_id/content(连同 id/artifact_id/kind 与两条 time 房型列)为列级 NOT NULL 强制:
-- task_id 是主检索维度与 uk_dedupe 首列、产物本体 content 亦不容悬空。
-- 关键后果:uk_dedupe 中可空的 node_id/attempt/content_hash 任一为 NULL,该行豁免
-- 唯一索引判重(MySQL NULL 互不相等)——空值行回放即重复、且脱离主检索维度;故领域构造器与
-- 发布校验(§3/§4)不放宽,仍强制 task_id+node_id+attempt+content+hash+creator。
-- storage 为存储介质元信息列(用户定稿,置于 content 前,可空、varchar(2048)):非 file 分支持恒为 inline,
-- file 分支必须与 FileContent.storage_type 同值——由构造器从 content 派生并强校验,不构成第二首领;
-- 发布路径必填,NULL 仅为容错/人工修数留口;2048 长度为介质元信息 JSON 化预留(当前只放介质名)。
-- 查询价值:inline 粗筛"内容自带"产物、外部介质产物分开运营,不用反序列化 content。
-- creator/modifier/gmt_create/gmt_modify 为表结构房型列(用户定稿):行不可变,
--   modifier 恒等于 creator、gmt_modify 恒等于 gmt_create,列保留纯为 schema 惯例兼容;
-- gmt_create 由仓储以领域 created_at(ms)同事务双写,DB DEFAULT 仅为 fallback(trajectory 同款惯例)。
-- 列名沿用已部署表的实际名 gmt_modify(非 gmt_modified,trajectory 部署形态同款命名)。
-- content 取 MEDIUMTEXT(16MB) 而非 TEXT(64KB):TEXT 分支装 bot 全文交付物(报表类轻松超 64KB),
-- 且 relay 上报的 text_content 不经 task_node_run_info.output(TEXT 档)没有天然截断保证;
-- 变长按需占用,存 1KB 只占 1KB,不为此预付代价。
CREATE TABLE IF NOT EXISTS `task_artifact` (
    `id`              bigint(20)    NOT NULL AUTO_INCREMENT                       COMMENT '主键ID',
    `artifact_id`     varchar(32)   NOT NULL                                      COMMENT '产物 ID(task-art-+uuid4hex16,全局唯一)',
    `artifact_name`   varchar(255)  NULL                                          COMMENT '业务展示名(NULL=未提供;file 产物发布时回落 file_name)',
    `task_id`         varchar(128)  NOT NULL                                      COMMENT '归属任务 ID(与报文定位交叉校验后落库;列级强制非空——主检索维度与 uk_dedupe 首列不许悬空)',
    `node_id`         varchar(128)  NULL                                         COMMENT '产出节点 ID(发布路径必填,NULL 仅为容错/人工修数留口;同 task_id 的空值豁免后果)',
    `attempt`         int           NULL                                         COMMENT '节点 harness_retries 快照(0 起;与报文交叉校验;发布路径必填,NULL 仅为容错——空值行豁免 uk_dedupe 判重)',
    `scope_session_id` varchar(512) NULL                                         COMMENT '图绑定协作会话 ID(图绑定优先,未绑定采纳上报值;NULL=均无)',
    `kind`            varchar(64)   NOT NULL                                      COMMENT '内容形态(text|file|url_link|code),governed 枚举',
    `storage`         varchar(2048) NULL                                         COMMENT '存储介质元信息:inline=内容本体在 content 列(text/url_link/code);bot_workspace|session_file=字节在外部介质,content 仅为引用(与 FileContent.storage_type 强一致,构造器推导校验;发布路径必填,NULL 仅为容错/人工修数留口,长度冗余预留给介质 JSON 化)',
    `content`         mediumtext    NOT NULL                                      COMMENT '内容分支 JSON(kind-tagged,tag=分支判别;不含字节/临时凭证/存储 handle;产物本体,列级强制非空;TEXT 家族禁显式 DEFAULT',
    `content_hash`    char(64)      NULL                                         COMMENT 'sha256(canonical_json(content)),uk_dedupe 成分;发布路径必填——NULL 行绕过幂等闸(MySQL 唯一索引 NULL 不判重)',
    `creator`         varchar(128)  NULL                                         COMMENT '产出者(bot_id/group_id;system=系统写入,如控制信号;NULL=未记录)',
    `modifier`        varchar(128)  NULL                                         COMMENT '修改者(行不可变:恒等于 creator,房型惯例列;NULL=未记录)',
    `gmt_create`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP            COMMENT '发布时间(仓储以领域 created_at 毫秒值双写,DB 默认仅 fallback)',
    `gmt_modify`      timestamp     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后修改时间(行不可变:恒等于 gmt_create,房型惯例列)',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_artifact_id` (`artifact_id`),
    UNIQUE KEY `uk_dedupe` (`task_id`, `node_id`, `attempt`, `kind`, `content_hash`),
    KEY `idx_scope_session` (`scope_session_id`, `gmt_create`),
    KEY `idx_task_kind` (`task_id`, `kind`, `gmt_create`),
    KEY `idx_task_name` (`task_id`, `artifact_name`)
) DEFAULT CHARSET = utf8mb4 COMMENT='task 产物不可变清单(spec 2026-10-20-task-artifact-domain)';
```

要点备注(索引设计,用户定稿检索维度:task_id / node_id / artifact_name / scope_session_id / kind):两道 `UNIQUE KEY` 兼任幂等机制(§5.3)与 `task_id`、`(task_id, node_id)` 检索(最左前缀,不重复建);三个二级索引补齐其余维度——会话(`scope_session_id`)、形态过滤(`task_id+kind`)、业务名检索(`task_id+artifact_name`,LIKE 前缀命中、`%中缀%` 不命中);等值列后追加 `gmt_create` 使"过滤后按时间倒序"直接走索引序;`content` 大文本不建索引;SQLite 单测不经此文件,由 ORM `Base.metadata.create_all` 同步建表。

---

## 7. 读侧最小暴露(本期唯一读侧改动)

原则:消费路径不切换(下游/Judge/OpenAPI 仍读 output dict),只让产物**可见**:

- `TaskNodeDTO`(`adapters/http/task/schemas.py:305`)新增 optional `artifacts: list[TaskArtifactDescriptorDTO]`,由 dashboard 读路径按 `(task_id, node_id)` 读时富化(取该节点全部 attempt 的产物,attempt 倒序)——协议仍是"只转协议",富化在 service 层;
- Descriptor 只含安全字段:`artifact_id / artifact_name / kind / content(kind-tagged 引用元数据:File 分支 storage_type+storage_id+file_name+file_id+file_type;UrlLink 分支 url+type;Code 分支 code_base_name+code_base_address+pr_link+pr_desc)/ attempt / creator / created_at`;
- 轨迹 HTML(`trajectory_html.py`)的「产物」块按 Descriptor 渲染文件名,不渲染任何 URL/下载链接(前端自拼);
- **OpenAPI v1 面的精确口径(方案取舍)**:共享的 `graph_to_dto` 同时供内部看板路由与 `GET /openapi/v1/collaboration/tasks/dashboard`(`openapi_v1/task/router.py:111/:135`)使用,`TaskNodeDTO` 加字段即**自动**出现在公开 OpenAPI 响应模型中——本方案接受这一**附加可选字段**的公开可见(与"看板产物清单可见"是同一诉求),不加新端点、不改任何既有字段的语义;OpenAPI 导出快照与相关导出测试在**同一变更内**更新。若评审认为公开面必须零变更,则替代方案是给 openapi_v1 路由派生独立 DTO——本期不选,理由:使读侧工作量翻倍且两条演化路径在阶段二必然合流。

---

## 8. 演进路线(本方案在整体迁移中的位置)

语雀文档的三阶段迁移在本方案的语境下:

- **阶段一(本方案)**:引入 Artifact + 回调契约 + 双轨记录(写侧 + 只读可见);
- **阶段二(后续)**:读侧切换——下游节点按 artifact 解析上游输入、Judge 消费 Descriptor、RuntimeInfo.output 退化为投影、OpenAPI 导出产物列表;
- **阶段三(后续)**:停止产出文本约定键、`Structured`/`Collection` 内容分支、`supersedes` 修订链、按需的字节物化/提取(文本提取产物,`derived_from` 血缘)。

---

## 9. 验收门禁

实现为单独 plan(tasks.md)推进,本节是验收口径:

- [ ] 回调契约:relay `ExecutionEventDTO.artifacts` 列表解析——扁平 item(恰好一个 content 字段/artifact_kind 一致/content JSON 合法/task_id·node_id·session_id·attempt 交叉校验/路径逃逸/非 http(s) 链接/超 64 条/DECLINED 携带)契约测试;非法整体拒绝不部分接受;集中 push/pull 报文无 artifacts 字段、行为逐字节不变;
- [ ] 领域:模型构造互斥校验、未知 kind fail-loud、不可变性、content_hash 稳定性;
- [ ] 双轨:文本/文件产物在集中/relay 写入点发布;重试后上一 attempt 产物仍在;版本冲突重放不产生重复行(幂等键);
- [ ] 事务:产物写失败 → 图变更整体失败(需构造 repo 异常注入的测试);contextvar 暂存跨重放正确清理;
- [ ] 持久化:SQL 与 ORM 同步(singlebox 可建表)、repository 契约测试、README provides/Boundary 增补、DI 绑定;
- [ ] 读侧:`TaskNodeDTO.artifacts` 富化 + HTML 产物块无 URL;OpenAPI 导出快照同步更新(附加可选字段);
- [ ] 领域语义:文本归约器含 `{"output": <str>}` 主通道形态;`skipped`/`notify_result` 产生 TEXT 产物(text=canonical JSON)且每 attempt 不超过 1+1 条;`file_type` 缺省推断与兜底值;
- [ ] 契约变更:`save_graph` 新增可选参后,全部现存实现/测试替身同步(检索所有以 `save_graph` 为签名的 stub);
- [ ] 缺席态:graph 仓储未接线(内存-only)时不发布、不报错;
- [ ] 架构守卫:`test_repository_contracts` / `test_task_module_responsibility_guards` / README provides 测试全绿;
- [ ] 回归:`uv run pytest tests/community/core/task tests/community/repository/task tests/community/adapters/http/task`(apps/backend 下,uv 环境)全绿;ruff changed files 清洁;
- [ ] 兼容:无 `artifacts` 字段的旧回调行为逐字节不变;output dict 内容与现状一致(剥离仅发生在产物块范围内)。

---

## 10. 本方案取舍汇总(用户可推翻的清单)

| # | 取舍 | 理由 |
|---|---|---|
| 1 | kind = 内容形态枚举 `TEXT + FILE + URL_LINK + CODE`(**用户定稿**),业务用途 kind 不引入 | 与内容分支一一对应;回调无业务分类信号,不预埋 |
| 2 | 不做图级产物(scope 只有 node 粒度) | graph.output 是投影,建产物引入选主问题 |
| 3 | attempt = harness_retries 快照 | 与 NodeActionEvent / relay 幂等键同口径 |
| 4 | dict 形态 output 不记文本产物 | 无 Structured 分支,不伪造语义,边界诚实 |
| 5 | 同事务强一致,不 post-commit 补写 | 产物是审计对象,失败不吞 |
| 6 | 幂等键 = (task_id, node_id, attempt, kind, content_hash) | 最后一道闸,与到达层幂等互补 |
| 7 | BBS 通道协议不动 | 其 output 为编排文本,无 bot 文件交付场景 |
| 8 | 读侧仅 TaskNodeDTO 富化 + HTML 无 URL | "写侧+记录"边界的最小可见性 |
| 9 | 产物 ID `"task-art-" + uuid4().hex[:16]`(**用户定稿**) | 遵循目录内前缀风格(tis-/bbs-),task- 前缀标明域归属 |
| 10 | 可选端口未接线 → 启动期不发布+警告;运行期持久化失败 → 上抛 | 区分"未启用"与"启用后失败"两个语义 |
| 11 | 产物行由图仓储在 `save_graph(published_artifacts=...)` 事务内嵌写入;TaskArtifactRepository 只读 | 事务不外泄;先例 `TaskCallbackModel` 内嵌写 |
| 12 | 幂等 select-first + 唯一键兜底(撞键走版本重放),非"冲突回滚再读" | prod plugin 无 SAVEPOINT,PG 冲突即废事务 |
| 13 | 文本归约器含 `output` 键、与 relay 投影为超集分叉 | 三大主通道文本都在 `{"output": str}`,不含则双轨落空 |
| 14 | 控制信号记为 TEXT 产物(text=canonical JSON 整 dict) | 信号值是 bool/嵌套 dict,非"单键字符串"可作为;审计链保留 | 
| 15 | 上报引用结构校验但不做归属核验(信任已鉴权回调方) | 字节读口有参与者 ACL;归属核验列为后续加固 |
| 16 | `TaskNodeDTO` 加字段经共享 `graph_to_dto` 自动出现在 openapi_v1 dashboard 响应;快照同批更新,不做 DTO 分叉 | 公开面附加可选字段 = 看板可见性同一诉求;分叉双倍工作量且阶段二合流 |
| 17 | `artifact_name`(str\|None):上报可选,FILE 回落 file_name,TEXT 默认 None;不进幂等键 | 文本产物获看板标题;业务名/物理名槽位分离;诚实边界见 3.6 |
| 18 | 产物上报只走 relay `/callback/report`,`ExecutionEventDTO` 顶层加 optional `artifacts` 列表(**用户定稿**);集中 push/pull 协议不动 | 接力链路是当前基线的主回调协议;`extra=forbid` 加可选字段兼容;集中通道后续按同构字段平移 |
| 19 | 新增 `URL_LINK`/`CODE` 内容分支(**用户定稿**):url/type 与 code_base_name/address/pr_link/pr_desc;url 类是产物内容本身,不受"禁临时下载 URL"约束;type 开放字符串;上报列表上限 64 | 链接/代码是协作产出的常见形态;字典四形态,governed 加值即演进 |
| 20 | 上报 item 为扁平自描述结构(**用户定稿**):artifact_name/artifact_kind/session_id/task_id/node_id/attempt + 恰好一个 content JSON 字符串;scope 四字段均可上报但全部交叉校验(attempt 不一致拒、session_id 图优先上报兜底) | 上报自描述、服务端校验不盲信;幂等键不被误导 |
| 21 | 独立建 `task_artifact` 表,不复用 `task_node_run_info`(与用户评估后确认,见 §6 段首) | run_info 是可变当前态行,产物是 append-only 清单;blob 化丢 uk_dedupe 且带来写放大 |

## 11. 待确认决策(开放问题)

1. `FileContent.file_type` 推断的扩展名映射表放领域层还是 adapters 层(倾向于领域层常量,单点维护);
2. 前端拼下载链接需要的元数据,`TaskArtifactDescriptorDTO` 的 File 分支是否够用(需前端确认);——涉及具体前端协议,后期对齐
3. 单 bot 工作区路径上报时,是否需要附带 `size`/摘要类字段(现读口不回这些,倾向于不加);
4. `creator`(str)对 group 场景的取值(assignee group_id vs 具体执行 bot)按写入点实际可得信息定,实现时对齐;`modifier`/`gmt_modify` 恒等于 `creator`/`gmt_create`(行不可变),不构成开放问题。