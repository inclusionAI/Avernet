# 独立启动与 Card 转换

这条入口只安装 Avernet 公共包即可运行，不需要 OCB、内部 SDK、账号或模型密钥。
默认使用 SQLite + Qdrant Local；离线示例向量用于验证流程，不代表真实语义效果或容量。
TC 继续使用原来的应用入口、Worker/Profile、recommend 和 Fuse，不自动切换到这套 Card 流程。

## 安装与启动

在 Avernet 仓库根目录使用 Python 3.10+：

```bash
python -m venv .venv-search
.venv-search/bin/python -m pip install './apps/bcsfuse/packages/search-core[http,qdrant]'
.venv-search/bin/bcsfuse-catalog --data-dir .data/catalog --port 8877
```

打开 `http://127.0.0.1:8877/docs`。数据保存在指定目录下：

- `cards.sqlite3`：源 Card、已发布 Doc 和索引任务表。
- `qdrant/`：本实例的查询索引。

只允许本机回环访问。这是本地开发入口；服务器部署应在公共组装函数中注入正式鉴权。
如需使用已启动的 Qdrant Server，可加 `--qdrant-url http://127.0.0.1:6333`；
连接失败不会自动退回 Local。不同实例应使用独立的 Qdrant 集合。

## 写入、搜索、更新与删除

创建 Card：

```bash
curl -X PUT http://127.0.0.1:8877/api/v2/agent-cards/demo \
  -H 'Content-Type: application/json' \
  -d '{"card":{"name":"账单助手","description":"查询消费账单"}}'
```

返回 202 表示已保存。后台加工后才能搜索；查询本实例状态：

```bash
curl http://127.0.0.1:8877/api/v2/agent-cards/demo/index-status
```

等待 `indexed_card_version` 等于 `card_version` 且没有 `index_error`，再搜索：

```bash
curl -X POST http://127.0.0.1:8877/api/v2/search \
  -H 'Content-Type: application/json' -d '{"query":"账单","limit":5}'
```

新 Card 应用只提供 `/api/v2/search`，不挂载重复的 recommend 或 preview 入口。部分更新只传需要修改的字段：

```bash
curl -X PUT http://127.0.0.1:8877/api/v2/agent-cards/demo \
  -H 'Content-Type: application/json' -d '{"card":{"description":"查询账单和消费明细"}}'
curl -X DELETE http://127.0.0.1:8877/api/v2/agent-cards/demo
```

删除同样由后台同步到索引；删除标记保留用于恢复和同步。详细语义见 [Card 接口](card-management-api.md)。
保持 `--data-dir` 不变，停止后重新启动会恢复已保存的 Doc，无需重新调用模型。

## 当前索引与数据数量

`GET /ready` 返回响应实例的 `instance_id`、实际使用的 `index_key` 和
`active_index`。版本管理入口未指定版本时，两者均为 `default`；指定时均为启动时加载的版本名。
这些值只代表响应实例，不是所有实例的汇总。

需要查询当前 Qdrant 集合的数据数量时使用 `GET /ready?include_counts=true`：

```json
{
  "ready": true,
  "instance_id": "catalog-local",
  "index_key": "default",
  "active_index": "default",
  "index_counts": {
    "vector_points": 50,
    "vector_matching_points": 50,
    "vector_mismatched_points": 0,
    "keyword_points": 50,
    "keyword_matching_points": 50,
    "keyword_mismatched_points": 0
  }
}
```

`*_points` 是向量/关键词集合的总条数，`*_matching_points` 是 payload.index_key
等于响应中 index_key 的条数，`*_mismatched_points` 是不同或缺少该字段的条数。
这些是从 Qdrant 实际读取的统计，不从 OB 或实例缓存推算。两路 mismatch 均为 0
表示本次未发现版本不符，但空集合也会是 0，不表示 Card 已全部入索引。
出现 mismatch 时仍返回诊断数据，不改变普通 `/ready` 的判断。只统计当前绑定的集合，
不包含旧版本集合，也不是所有实例之和。一张 Card 可生成多个 Doc，因此数量不一定等于 Card 数。
它们是查询时的数量，不作为全体 Card 已同步完成的判断。
普通 `/ready` 不执行计数，平台探活无需改动。计数失败时带参数的请求返回 503，
失败响应仍带 ready=false、instance_id、index_key、active_index 和 detail；
服务未就绪时也会尝试返回请求的真实计数。不会把失败伪装成 0；不提供统计能力的其他索引实现返回 501。Qdrant Local 和 Server 均支持。

## 默认转换与业务替换

默认只读取 `name`、`description`、技能的 `name/description/tags`，按固定顺序组成一条 `main` Doc。
`search_text` 用于关键词和向量召回，默认 `rerank_text` 与其相同。不会把完整 JSON、调用地址、鉴权资料拼进索引。
可选字段缺失或不是文本时跳过；完全没有可检索文本时，保存仍成功，加工状态会报错。
筛选标签只取组装入口声明的字段；本地入口默认声明 `resource_type`。

业务通过已有接口替换转换器即可。例如：

```python
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText

class BusinessConverter:
    version = "business-v1"

    def convert(self, snapshot):
        card = snapshot.source_card
        return CardProjection(
            [DocumentText("main", card["name"], card.get("description") or card["name"])],
            {"resource_type": (snapshot.resource_type,)},
        )
```

将实例传给 `create_local_app(..., document_converter=BusinessConverter())`，或正式组装函数
`create_catalog_search_app`。业务只决定文本与标签；向量复用、Doc 成组发布、删除旧 Doc、
增量同步和恢复由公共流程负责。一张 Card 可生成多条 Doc，检索仍按 Card 聚合。
真实模型、精排与业务返回结构在正式组装入口注入，本地演示不自动接入它们。
