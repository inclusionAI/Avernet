# Cron 目标身份字段更名

日期：2026-10-19。用户要求将create_by_id更名为operator_id。

## 需求与计划

本轮修改Avernet PR #2587的Cron创建/查询/管理请求、响应目标字段及领域接线，operator_id仍表示原字段的创建人/Caller身份。现有真实登录操作者在Cron定向链路内改名authenticated_user_id，只从认证上下文传入，保留跨身份ADMIN授权、实例归属校验及失败不回退。
不修改独立Cron管理员接口（其operator_id本来就是真实管理员），不修改Engine Adapter；不为旧字段增加兼容别名。前端交互文档同步新字段，旧OCB实验实现作为历史参考不在本Avernet PR提交。

## 验证

覆盖新字段schema验证、真实HTTP创建与全管理接线、响应字段、Caller解析、本人/代建/拒绝代建、伪造认证字段不得提升权限；新增日志清楚区分operator_id和authenticated_user_id。运行Cron/HTTP实际runner、DI/架构及静态检查；正常提交push现有PR，远端CI单独报告。

## 既有CI修复

上一head81759e87ff的Backend job118044397967报告2个Cron公开接口测试失败：构造CronRelayService漏runtime_bindings/collaborators。按最新依赖补齐测试fixture，不放宽权限、状态码或覆盖率门禁。新增伪造身份HTTP回归遵守现有入口ADMIN权限规则，期望403。

## 本轮结果

2594 passed/134warnings：Cron、真实HTTP endpoint runner、公开Cron错误映射、DI与架构测试。新增HTTP403与领域代建拒绝验证新请求身份不替代认证身份；新operator_id响应与双身份日志断言通过。修改Python ruff/diff-check通过；当前远端CI待新head执行，不把历史全量验证作为新head结果。
