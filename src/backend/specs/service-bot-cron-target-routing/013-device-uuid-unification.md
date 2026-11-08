# Cron 实例字段统一device_uuid

日期：2026-10-19。用户要求device_id统一device_uuid。

## 计划与边界

Cron创建、查询、管理请求/响应/日志统一device_uuid，移除device_id别名和重复字段；只改Cron业务契约，不改底层设备记录原生device_id或其他模块设备ID。保留既有running/runs在未指定operator_id时按device_uuid筛选的历史响应结构；指定operator_id时保持Caller定向路径。Engine无需修改。同步契约文档与测试，正常提交push PR #2587。

## 验证

检查schema仅公开device_uuid、请求/响应字段不重复；多实例归属/错误UUID/无实例拒绝；Caller与真实认证身份分离；全CRUD及历史running/runs原响应兼容；Cron、HTTP endpoint runner、公开错误映射、DI/架构回归，ruff和diff检查。历史报告保持原执行记录，不把旧测试冒充本轮结果。

## 验证结果

2594 passed/134warnings：Cron、实际HTTP endpoint runner、公开错误映射、DI、架构最终复跑全部通过；独立282项Cron定向回归通过。实际HTTP创建/管理请求带device_uuid，响应与schema无device_id，UUID-only running/runs旧筛选测试通过。首轮唯一失败为query数值转字符串后的未知实例应404而非类型错误400，改为空UUID校验用例后完整复跑通过。ruff/diff-check通过。
