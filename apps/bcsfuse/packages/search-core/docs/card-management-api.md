# Card 通用运维接口

实现归属：Avernet 的 `search-core` 包。OCB 复用这份实现；TC 当前未接入，
现有 sync/Worker/Profile 接口不变，后续迁移时再由 TC 显式启用。

Card 是完整业务 JSON。下列字段只是示例，不是白名单；新增字段、嵌套对象和数组
都会保存，并通过读取接口返回。索引正文和前置标签由各业务构建器决定。
修改业务 `status`、`deleted`、`version` 不等于删除 Card。

| 用途 | 方法与路径 |
| --- | --- |
| 创建或更新 | PUT `/api/v1/agent-cards/{id}` |
| 删除 | DELETE `/api/v1/agent-cards/{id}` |
| 读取完整 Card | GET `/api/v1/agent-cards/{id}` |
| 查询索引加工状态 | GET `/api/v1/agent-cards/{id}/index-status` |

Agent 与 Group 共用这些接口。`id` 为 1～128 个字符，不允许空白字符。
调用方不传保存版本。202 只确认数据已提交，索引由应用的后台任务处理。
读取 Card 只查源表，索引状态由当前处理实例提供，不能代表所有实例。

## 1. 创建或更新 Card

`PUT /api/v1/agent-cards/test:billing`

新建示例：

```json
{
  "resource_type": "agent",
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
| card | 是 | 非空 JSON 对象；创建时提供业务资料，更新时只放要修改的字段 |
| resource_type | 否 | agent/group；新建省略时为 agent，更新省略时保留原类型；已有 ID 不能改成另一种类型 |
| search_config | 否 | 独立运维配置，普通 BCN 资料提交无需填写；省略不改变已有配置，详见文末 |

不接收外层 source_version、expected_revision。`card.version` 若存在，仍只是业务自己的字段，服务不要求修改它才能更新资料。

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
{"saved": true, "saved_revision": 1}
```

`saved_revision` 由服务端生成，仅供排查，不需要在后续请求里传回来。重复提交当前相同内容可直接成功，不会因重试而重新增加版本。这里只确认持久化成功，不返回固定的 pending 状态。

同一 ID 的并发更新由服务端处理，未提供的字段仍保留；同时修改同一字段时，以数据库最终提交顺序为准。服务能阻止内部旧加工任务覆盖新数据，但无法自行识别上游网络迟到的旧业务请求。

Group 创建示例：`PUT /api/v1/agent-cards/bcs_grp_example`

```json
{
  "resource_type": "group",
  "card": {
    "groupId": "bcs_grp_example",
    "name": "旅行服务群",
    "description": "协作规划行程"
  }
}
```

## 2. 删除 Card

`DELETE /api/v1/agent-cards/test:billing`

不需要请求体，也不需要版本参数。成功返回 **202**：

```json
{"deleted": true}
```

对象已经删除或者本来不存在，都按成功处理。删除后读取 Card 返回 404；已存在记录的加工状态仍可查询，方便确认后台索引清理情况。删除是逻辑删除，服务内部保留同步所需记录，不承诺物理擦除。

同一 ID 如果后来重新创建，再调用 DELETE 会删除当前对象；没有请求版本时不能把迟到的旧 DELETE 自动识别为“只针对上一代对象”。

## 3. 读取 Card

`GET /api/v1/agent-cards/test:billing`

成功返回 **200**：

```json
{
  "resource_type": "agent",
  "card": {"name": "账单助手", "description": "查询消费账单"},
  "search_config": {
    "enabled": true,
    "match_modes": ["exact", "keyword", "semantic"],
    "keyword_operator": "any",
    "action": "prefer",
    "priority": 0
  },
  "saved_revision": 1
}
```

读取 Card 只查询 Card 表；即使索引产物表暂时不可用，仍能读到已保存的业务资料。card 返回保存后的完整业务对象，包括未被此次更新修改的字段。search_config 和 saved_revision 位于 Card 外，不是业务字段，也不需要整体复制回更新请求。不存在或已删除返回 **404**。

## 4. 查看索引加工状态

`GET /api/v1/agent-cards/test:billing/index-status`

成功返回 **200**。该接口需要额外读取索引产物表，因此与读取 Card 分开；正常读 Card 只查 Card 表，不加载向量或索引产物。常用字段如下：

| 字段 | 说明 |
| --- | --- |
| agent_id | 路径中的对象 ID，Group 也沿用此字段名 |
| saved_revision | 已保存到数据库的版本，由系统生成 |
| deleted | 是否由 DELETE 删除，与 card.deleted 业务字段无关 |
| prepared | 当前保存版本的索引产物是否已生成 |
| preparation_error | 加工错误码，空字符串表示无记录的加工错误 |
| local_applied_revision | 本实例已经应用的版本；等于 saved_revision 表示本实例已跟上 |
| local_error | 本实例应用索引时的错误码 |
| instance_id | 响应请求的实例 |
| environment / index_key / operations_text_revision | 环境、索引版本及内部规则观察版本，用于诊断；接入版本管理的宿主直接使用版本名作为 index_key |

经负载均衡查询可能落到不同实例，不能用一次响应证明所有实例已完成。资料尚未创建过返回 404。

构建、切换和向量复用见 [索引版本管理](index-rebuild.md)。Qdrant 的向量和关键词
point payload 都保存所属 `index_key`，由系统在写入和恢复时补充，不属于业务 Card，
不参与正文或向量计算。它不表示所有实例都已经启用该版本。

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
