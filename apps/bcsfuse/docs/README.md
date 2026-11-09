# BCSFuse 文档入口

通用能力和 TC 的文档由 Avernet 维护。支付宝业务、TR、内部平台、发布配置和内部压测由 OCB 维护。
同一契约只维护一份正文；其他文档引用它。TC V2 提供 Card 管理与单张 Card 的索引状态查询。

## 先按用途找文档

| 用途 | 唯一正文 |
| --- | --- |
| 了解公共包、TC 和宿主的职责 | [架构边界](architecture/search-core-boundary.md) |
| 创建、更新、删除和读取 Card | [Card 通用运维接口](../packages/search-core/docs/card-management-api.md) |
| 通用 v2 搜索与推荐 | [搜索 API](../packages/search-core/docs/search-api.md) |
| 组装模型、索引和 Card 构建器 | [Provider / 插件契约](../packages/search-core/docs/provider-contracts.md) |
| 索引重建与版本选择的通用能力 | [索引重建](../packages/search-core/docs/index-rebuild.md) |
| 调用 TC 现有产品接口 | [外部产品 API](api/api-external.md) |
| 调用 TC 管理和管理员接口 | [管理 API](api/api-reference.md) |
| TC 应用组装、日志与兼容规则 | [应用组装契约](architecture/composition-contract.md) |
| TC Card/Doc 迁移 | [迁移边界](architecture/tc-card-migration.md)、[部署与导入](deploy/tc-card-migration.md) |
| 向量持久化与同步 | [存储契约](architecture/vector-persistence-contract-v1.md)、[Qdrant 模式](qdrant-modes.md) |

## 安装与运行

[应用 README](../README.md) 是启动入口。详细部署指南保留中英两种语言，它们是翻译版本，不是两套配置：

- 通用部署：[中文](deploy/open-source-deployment-guide-chinese.md) / [English](deploy/open-source-deployment-guide.md)。
- macOS 本地：[中文](deploy/macos_local_chinese.md) / [English](deploy/macos_local.md)。

公司专用域名、ACI、ZDAS/MIST/DRM/TR 操作不放在这些公共指南里。

## 历史与例外

[归档](archive/README.md) 只保存独立的执行证据；完成的实施清单不再作为现行方案重复维护。
架构例外位于 [waivers/](architecture/waivers/)，按各文件期限和适用范围使用。

## 本次合并与归属

- Card CRUD 的完整正文只在 search-core 维护，宿主文档只写业务差异和引用。
- TC API 参考中的重复产品接口描述已移除，统一引用 api-external；管理接口仍独立维护。
- 已完成的 retrieval-extraction-plan 删除，执行结果归档，当前结论归入架构边界。
- TC Card 迁移计划从 OCB 移入 Avernet；内部数据迁移步骤和验证记录留在 OCB。
- 两篇含公司部署上下文的旧 ACK 文档合并到 OCB 历史归档；公共安装指南继续在这里维护。
