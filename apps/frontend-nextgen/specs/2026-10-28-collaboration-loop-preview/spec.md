# 自定义协作固定循环与嵌套循环预览

- 来源：Avernet PR #2339、#2678 的 frontend 消费端实现。
- 范围：仅 `apps/frontend-nextgen`；不修改 BCS 执行语义、服务端开关、远程 Panel 或副屏建群入口。
- 契约：[协作定义校验 API](../../../bcs/api-contracts/v1/openapi/collaboration-definitions.yaml)。

## 行为

1. 保留普通 acyclic DAG 的布局、角色绑定与高亮。
2. 对 hierarchical 定义，使用服务端显式返回的 `execution_graph_mode: acyclic` 排序执行图。
   不解析 execution node ID、不改写原始 YAML 或执行元数据。
3. 有完整 Loop descriptor 时按逻辑结构展示一个循环体，区分 continue、break、exhausted，
   使用服务端展示名称，保留实际 outcome。单次循环不显示返回边。
4. 嵌套布局按 `loop_path` 的完整定义路径区分父子和同名 Loop，按父子容器展示，
   支持同层多个 Loop 和 HumanInput 节点；父容器先于子容器交给 ReactFlow。
5. 缺少 descriptor 的历史编译图保留展开视图；不支持的图模式与无效图显示明确错误。
6. 循环限制折叠展示；绑定变化保留视口，结构与容器尺寸变化重新适配视图。
7. 保留服务端诊断 code、path、message、hint。`VALIDATION_ONLY_FEATURE` 允许校验、预览和绑定，
   但禁止确认建群；其他 warning 仅提示。重新编辑、失败、重新校验不能沿用过期的执行准入状态。

## 验证

- 复用 BCS 固定循环 fixture 验证编译图、逻辑布局、出口与原始数据不变。
- 对齐旧 frontend 的多循环、100 轮折叠、嵌套同名路径、三层 HumanInput 与并列子循环用例。
- 在 nextgen 建群弹层验证完整的校验、预览、绑定、执行准入与 YAML 提交流程。
- 2026-10-28：Open Core 检查、工程门禁、类型检查、lint 和生产构建全部通过。
- 全量 Jest：598 个套件、4585 项测试全部通过；功能及受影响调用处的单独回归：9 个套件、72 项测试通过。
- 浏览器使用生产预览组件核验固定循环、嵌套父子容器、返回线、出口标签和折叠限制说明。
- 移除遮蔽依赖正式类型的 ReactFlow 手写声明，并为现有 WorkflowDagView 补齐节点数据约束；不改变其运行行为。

## 兼容与部署

消费既有 API 的增量字段，不新增服务端契约、依赖或配置。
真实执行仍由 BCS `loop_execution_enabled`、`nested_loop_execution_enabled` 及资源上限决定。
本次不声称已验证线上运行、CDN 版本或副屏到建群弹层的链路。
