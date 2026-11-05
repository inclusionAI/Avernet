# BCSFuse 公共能力与应用边界

Avernet 维护可独立安装的 `search_core` 和 TC 应用 `app`。宿主维护内部平台连接及自己的业务应用。
**TC 在业务组装中挂载 v2 Card/搜索与配置接口，固定使用 TC 新表。旧 v1 通过 TC 专属配置整体选择旧表或新表，不向共享 search-core 加入切源逻辑。**

## 代码归属

| 内容 | 归属 |
| --- | --- |
| 通用搜索契约、关键词/向量召回、融合、可选精排 | Avernet search-core |
| Card 管理 HTTP 接口、按字段合并、内部版本控制、SQL 保存与删除 | Avernet search-core |
| 默认 Card 转换器、CardDocumentConverter / CardProjection、Doc 存储与模型/索引接口 | Avernet search-core |
| Doc 加工与成组发布、增量同步/核对、恢复、缓存/流控/降级、Qdrant 进程生命周期 | Avernet search-core |
| TC 的 Worker/Profile、ID 优先、分片聚合、生命周期和 Fuse | Avernet app |
| 某业务 Card 字段的含义、正文和前置标签选择 | 对应应用的构建器 |
| 私有数据库、模型协议、鉴权、配置订阅、TR 和部署组装 | 内部宿主 |

公共包不判断调用方属于哪个业务，不导入 OCB/内部 SDK，不通过安装或导入自动启动服务。
默认转换器选择名称、描述、技能名称/描述/标签，生成一条 Doc；业务可替换转换器，返回多条 Doc 和明确的筛选标签。
业务 Card 是完整 JSON，status、deleted、version 只是业务字段；运维删除由 DELETE 处理。
源数据、加工画像、运行状态、索引产物各有职责，不能用一种数据覆盖另一种。

## 公共模块

```text
apps/bcsfuse/
├── src/app/                   TC 应用与组装入口
├── packages/search-core/
│   └── src/search_core/
│       ├── api/               Service API
│       ├── core/              检索与 Card 通用服务
│       ├── application/       可选的公共搜索运行流程
│       ├── catalog/           Card 模型、默认转换器、Doc 加工与同步
│       ├── bootstrap/         公共组装与独立本地入口
│       ├── plugin_api/        存储、模型、索引、构建器契约
│       ├── providers/         公开协议实现
│       ├── config/            显式配置装配
│       └── adapters/http/     可选 HTTP 适配器
└── docs/                      TC 与公共应用文档
```

search-core 可独立安装；Card 模型/存储使用 cards 可选依赖，HTTP 使用 http 可选依赖。
只导入契约不会连接数据库、模型或启动后台任务；具体依赖在组装入口选择并管理生命周期。

## 契约与启用

- [Card 运维契约](../../packages/search-core/docs/card-management-api.md)：调用方不管理保存版本，部分更新保留未传字段；202 表示提交完成，不是全实例索引生效。
- [公共搜索契约](../../packages/search-core/docs/search-api.md)：HTTP v2 为显式启用入口，不替换 TC 原 v1。
- [Provider 契约](../../packages/search-core/docs/provider-contracts.md)：Service API 和 Plugin API 分开定义；业务构建器选择正文和标签。
- [TC 组装契约](composition-contract.md)：保留应用上下文、诊断、旧流程及扩展入口的行为。

可信授权范围只能由身份适配提供；None 表示明确不限制，空集合表示无权访问。
请求中的业务过滤不能授予权限，过滤在 top-k 前生效，返回前检查补充校验当前状态。
通用检索失败传播 SearchUnavailable；各业务决定降级策略，不能把旧 TC 的空结果语义默认为公共规则。

Qdrant 支持 Local 与 Server，两者配置与能力边界见 [Qdrant 模式](../qdrant-modes.md)。
模型/维度、持久化产物与查询索引必须匹配；独立同步任务不能共写同一对索引集合。
TC 的旧 Profile 聚合和业务门槛不因复用公共 provider 自动切换。

## 构建与兼容

应用与公共包从同一不可变 Avernet 版本构建，宿主选择经过组合测试的版本。
公共启动使用 app.* 和 search_core.*；不提供旧 src.* 包别名。
应用可以分别发布；回退应恢复已验证的应用/公共依赖组合，数据变化另行核对。
TC 迁移遵循 [Card / Doc 迁移边界](tc-card-migration.md)和关联的同实例方案，代码已实现，本地验证不代表已部署或完成线上迁库。

独立导入、契约、存储和 TC 兼容测试用于验证上述边界；历史执行结果见 [验证归档](../archive/validation/retrieval-extraction-20261027.md)。
本地合成模型或临时索引测试不是生产效果、容量或已部署的证据。

## 不依赖 OCB 的本地链路

```text
HTTP Card 写入 → SQLite 源 Card
                       ↓ 公共后台加工（默认或业务转换器 + Embedding）
                 SQLite 已发布 Doc → 本地 Qdrant
                                             ↓
HTTP 搜索/推荐 → 公共检索流程 → Card 元数据回查 → 返回结果
```

`search_core.bootstrap.local` 组装 SQLite、Qdrant Local 和离线示例向量；
真实业务在组装入口替换转换器、模型、索引与鉴权，复用同一加工和检索流程。
具体步骤见 [独立启动指南](../../packages/search-core/docs/local-catalog.md)。
公共能力抽取本身不改变支付宝历史 index_key；TC 的新表、入口兼容和后端选择由 TC 业务组装负责。
