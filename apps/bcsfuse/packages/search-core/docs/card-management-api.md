# Card 通用运维接口

新 Card、索引运维和搜索接口统一使用 `/api/v2`。原 `/api/v1/agent-cards`
和 `/api/v1/index-builds` 路径不再挂载，调用方需随服务升级修改 URL；
请求体、响应字段和业务逻辑保持不变。TC 现有旧接口不受影响。

Avernet 的 `search-core` 提供通用 Card 读写。TC V2 固定使用 Card/Doc 存储，V1 按已有迁移配置选择存储。

Card 是完整业务 JSON。下列字段只是示例，不是白名单；新增字段、嵌套对象和数组
都会保存，并通过读取接口返回。索引正文和前置标签由各业务构建器决定。
修改业务 `status`、`deleted`、`version` 不等于删除 Card。
`providerName`、显式 null 和未知字段也保留在 Card 内，不提升为运维字段。

| 用途 | 方法与路径 |
| --- | --- |
| 创建或更新 | PUT `/api/v2/agent-cards/{id}` |
| 删除 | DELETE `/api/v2/agent-cards/{id}` |
| 读取完整 Card | GET `/api/v2/agent-cards/{id}` |
| 查询索引加工状态 | GET `/api/v2/agent-cards/{id}/index-status` |
| 修复当前版本的一张 Card | POST `/api/v2/agent-cards/{id}/rebuild` |

Agent 与 Group 共用这些接口。`id` 为 1～128 个字符，不允许空白字符。
调用方不传保存版本。202 只确认数据已提交，索引由应用的后台任务处理。
读取 Card 只查源表，索引状态由当前处理实例提供，不能代表所有实例。

## 1. 创建或更新 Card

`PUT /api/v2/agent-cards/test:billing`

新建示例：

```json
{
  "resource_type": "agent",
  "labels": {"visibility": ["private"], "user_visibility": ["protected"]},
  "card": {
    "name": "账单助手",
    "description": "查询消费账单",
    "status": "published",
    "version": "1.0.0",
    "skills": [{"id": "billing", "name": "账单查询", "tags": ["消费记录"]}]
  }
}
```

| 请求字段 | 是否必填 | 说明 |
| --- | --- | --- |
| card | 新建必填 | 完整业务 JSON；更新时允许只传 labels |
| labels | 按业务约定 | 与 card 同级的字符串数组映射。按键合并，传数组替换该键，空数组删除普通标签，GET 返回完整标签。标签键支持字母、数字、下划线和连字符。 |
| resource_type | 否 | agent/group；新建省略时为 agent，更新省略时保留原类型；已有 ID 不能改成另一种类型 |

不接收外层 source_version、expected_revision 或 search_config。card.search_config 若由业务传入，仍原样保存，不控制运维行为。`card.version` 若存在，仍只是业务自己的字段，服务不要求修改它才能更新资料。

只改标签示例：`{"labels":{"topic":["invoice"],"department":[]}}`。标签随 Card 版本发布，原检索正文相同可复用已保存的向量。查询使用 `filters["labels.topic"]=["invoice"]`。

只改描述：

```json
{"card": {"description": "查询账单和消费明细"}}
```

名称、技能及其他没传的字段保持不变。具体更新规则：

- 普通字段：传了就替换，没传就保留。
- 对象：逐层更新。例如原来 `attributes={"region":"cn","category":"payment"}`，传 `attributes={"region":"us"}`，category 保留。
- 数组：整组替换。例如传 `skills=[]` 会清空技能，省略 skills 则保留技能。
- 空对象 `{}` 不会清掉已有子字段。显式 null 会保存为 JSON null，不等于“没传”，也不代表删除 Card；字段值仍需符合对应业务构建器的要求。
- 删除后再次 PUT 会重新创建业务 Card，不把删除前的旧业务字段合并回来。

成功返回 **202**：

```json
{"saved": true, "card_version": 1}
```

`card_version` 由服务端生成，仅供排查，不需要在后续请求里传回来。重复提交当前相同内容可直接成功，不会因重试而重新增加版本。这里只确认持久化成功，不返回固定的 pending 状态。

同一 ID 的并发更新由服务端处理，未提供的字段仍保留；同时修改同一字段时，以数据库最终提交顺序为准。服务能阻止内部旧加工任务覆盖新数据，但无法自行识别上游网络迟到的旧业务请求。

Group 创建示例：`PUT /api/v2/agent-cards/bcs_grp_example`

```json
{
  "resource_type": "group",
  "labels": {"visibility": ["public"]},
  "card": {
    "groupId": "bcs_grp_example",
    "name": "旅行服务群",
    "description": "协作规划行程"
  }
}
```

## 2. 删除 Card

`DELETE /api/v2/agent-cards/test:billing`

不需要请求体，也不需要版本参数。成功返回 **202**：

```json
{"deleted": true}
```

对象已经删除或者本来不存在，都按成功处理。删除后读取 Card 返回 404；已存在记录的加工状态仍可查询。

删除状态写入 Card 表独立的 `is_deleted` 运维列，DELETE 不修改 `card_json`，也不修改业务 `source_card.deleted/status`。
同一个事务会把**同环境、同 Card 的所有 index_key 下的 Doc** 标记为 `is_deleted=1`，推进 Doc 版本和修改时间，并使旧加工任务失效；任何一步写入失败都回滚并返回错误。
各运行实例通过已有同步机制删除 Qdrant 中对应的向量和关键词点。未运行的旧版本在下次启动恢复时也会处理删除，切换回去不会恢复已删除的 Card。
Doc 行保留为删除记录，供暂时离线的实例补同步，不承诺物理擦除；202 表示数据库删除事务已提交，不表示所有实例的 Qdrant 已同步完成。

部署使用独立删除列的版本前，需由宿主升级数据库。SQLite 本地初始化会将旧快照的顶层删除标记一次性迁移到列；运行时只以该列为准。

同一 ID 如果后来重新创建，再调用 DELETE 会删除当前对象；没有请求版本时不能把迟到的旧 DELETE 自动识别为“只针对上一代对象”。

## 3. 读取 Card

`GET /api/v2/agent-cards/test:billing`

成功返回 **200**：

```json
{
  "resource_type": "agent",
  "labels": {"visibility": ["private"], "user_visibility": ["protected"]},
  "card": {"name": "账单助手", "description": "查询消费账单"},
  "card_version": 1
}
```

读取 Card 只查询 Card 表；即使索引产物表暂时不可用，仍能读到已保存的业务资料。card 返回保存后的完整业务对象，包括未被此次更新修改的字段。resource_type 和 card_version 位于 Card 外，不是业务字段，也不需要整体复制回更新请求。不存在或已删除返回 **404**。

## 4. 查看索引加工状态

`GET /api/v2/agent-cards/test:billing/index-status`

成功返回 **200**。该接口需要额外读取索引产物表，因此与读取 Card 分开；正常读 Card 只查 Card 表，不加载向量或索引产物。常用字段如下：

| 字段 | 说明 |
| --- | --- |
| agent_id | 路径中的对象 ID，Group 也沿用此字段名 |
| card_version | 已保存到数据库的版本，由系统生成 |
| deleted | 是否由 DELETE 删除，与 card.deleted 业务字段无关 |
| doc_ready | 当前保存版本的索引产物是否已生成 |
| doc_error | 本 index_key 最近记录的加工错误；其他版本的构建失败不显示在这里，错误归属不符时为空。完整版本任务错误查询 index-builds |
| indexed_card_version | 本实例最近成功同步的 Card 版本；是内存同步记录，不是实时 Qdrant 核验 |
| index_error | 本实例最近一次索引同步的错误码；与本次 Qdrant 读取错误分开 |
| instance_id | 响应请求的实例 |
| environment / index_key | 环境及本实例启动时选定的索引版本；不是从 Qdrant 查询的值 |
| doc_version / indexed_doc_version | 数据库主 Doc 版本 / 本实例最近成功同步的主 Doc 版本；Card 不变而重建 Doc 时，Doc 版本仍会增加 |

经负载均衡查询可能落到不同实例，不能用一次响应证明所有实例已完成。资料尚未创建过返回 404。

该接口直接返回两份内容，帮助核对“生成了什么”和“实际检索什么”：

- `documents`：数据库当前 index_key 下该 Card 的所有 Doc，包括主 Doc、扩展问题 Doc 和删除记录。每条包含 `id`、`doc_version`、`deleted`、完整 `search_text`、`filters` 和向量摘要 `vector`。
- `qdrant.vector_documents`：本实例实际向量集合中的点，包含实际 `index_key`、`search_text`、`filters` 和 `vector`。
- `qdrant.keyword_documents`：本实例实际关键词集合中的点，包含实际 `index_key`、`search_text` 和 `filters`。不返回稀疏向量。
- `vector` 只包含 `dimension`（实际向量长度）和 `preview`（前 8 个数值）。Qdrant 可能归一化向量，预览数值不同不一定表示出错；预览也不能证明语义质量。
- `filters` 展示本索引声明的前置标签和 Doc 中记录的标签；不把业务 Card 的所有字段都当作过滤条件。
- `qdrant.status=ok` 表示这次读取成功，列表为空才表示没有找到点。`unavailable` 表示读取失败，`error=QDRANT_READ_FAILED`；`unsupported` 表示宿主索引不支持此诊断。失败时列表不代表实际数量，数据库内容仍可查看。
- 顶层 `index_key` 是本实例选定的版本；`qdrant` 每个点的 `index_key` 来自实际 payload，缺失时为 null，不会拿配置值填充。即使 `indexed_*` 与当前版本一致，这里仍可能发现外部删除或错误写入。

本次查询不重新构建 Doc、不调用 embedding 模型，也不修复数据。数据库与两个 Qdrant 集合分别读取，不保证并发更新期间是同一时刻的快照。

内容片段示例：

```json
{
  "documents": [{
    "id": "test:billing", "doc_version": 1, "deleted": false,
    "search_text": "账单助手\n查询消费账单", "filters": {"resource_type": ["agent"]},
    "vector": {"dimension": 1024, "preview": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]}
  }],
  "qdrant": {
    "status": "ok", "error": "",
    "vector_documents": [{
      "id": "test:billing", "index_key": "default",
      "search_text": "账单助手\n查询消费账单", "filters": {"resource_type": ["agent"]},
      "vector": {"dimension": 1024, "preview": [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]}
    }],
    "keyword_documents": [{
      "id": "test:billing", "index_key": "default",
      "search_text": "账单助手\n查询消费账单", "filters": {"resource_type": ["agent"]}
    }]
  }
}
```

对外统一使用 `card_version`、`indexed_card_version`、`doc_version`、`indexed_doc_version`、`doc_ready`、`doc_error`、`index_error`；旧响应字段名不再输出。请求体和数据库结构不变，调用方若读取旧响应字段需同步更新。

构建、切换和向量复用见 [索引版本管理](index-rebuild.md)。Qdrant 的向量和关键词
point payload 都保存所属 `index_key`，由系统在写入和恢复时补充，不属于业务 Card，
不参与正文或向量计算。它不表示所有实例都已经启用该版本。

## 5. 修复当前版本的一张 Card

`POST /api/v2/agent-cards/test:billing/rebuild`，请求体 `{"index_key":"default"}`。
先从本实例状态取得 index_key；接口只修复响应实例当前加载的版本，不能用它选择或切换版本。
成功返回 200：`{"published":true,"index_key":"default","card_version":1}`。
这表示中心产物已提交，Qdrant 随后台同步生效；原始业务 Card 不改写。
目标版本与响应实例不符返回 409，说明为 `requested index_key is not loaded on this instance`；
并发加工或资料变化也返回 409；模型或存储失败返回 503，原有完整产物保留。
该操作会调用模型，详见 [索引版本管理](index-rebuild.md)。

## 错误与启用方式

- 400：运维元数据不合法。
- 403：运维鉴权未通过；由宿主注入鉴权实现。
- 404：Card 不存在或已删除；删除不存在的 Card 仍返回 202。
- 409：试图改变已有 ID 的资源类型。
- 422：请求结构不合法，例如空 Card、非法 ID、外层版本参数。
- 503：数据库或索引状态读取失败，不会返回伪成功。

`create_card_router(service, authorize_operator)` 仅创建路由，不自动挂载、不启动后台任务。
宿主在组装入口注入 `CardManagement` 服务和鉴权，再显式挂载。
`CardManagementService` 提供通用读写；运行时实现 `status(id)`，报告自身的加工情况。
应用可以通过 `status_model` 扩展索引诊断响应；不改变 Card 的业务字段。

HTTP 无关的协议与持久化约定见 [provider contracts](provider-contracts.md#card-management)。

## 启动与后台补齐

启动只把当前索引版本已有的 Doc（正文、标签和向量）恢复到 Qdrant，
不等待所有 Card 加工完成。`/ready` 成功表示已有索引恢复完成且服务可查询，
不表示所有 Card 都已入索引；没有已有 Doc 时可以作为空索引就绪。
后台沿用版本比较，只补处理缺失或落后的 Card，失败后继续重试，重启也可接续。
Qdrant 同步失败仍会阻止就绪。首次选择 `default` 只保存配置，不自动登记全量重建任务。
