# 移除 publish-ignore 独立签名依赖

## 需求与边界

按用户要求，Backend 调用 Engine 不再依赖此功能单独引入的 Ed25519 密钥。保留现有 Bot 权限、共享 binding/connection 解析、BaaS/ARCA 连接认证、运行身份检查及固定文件路径防护。不修改绝对路径规则，不部署或重启实例。

## 计划

1. 添加无密钥、无 authorization 请求的真实 Engine 文件写入回归，先验证失败。
2. 删除 Backend 签名及 Engine 验签、密钥配置和 authorization 请求字段；保留 request_id，重复请求记录改为从首次消费时起保留 300 秒。
3. 更新双 provider、三 stage 合同测试和日志测试，运行 Backend/Engine 测试及静态检查。

## 信任与发布边界

Engine 接口依赖既有运行时入口认证，不再独立证明调用方一定是 Backend；不能将 expected_target 身份匹配视为调用方鉴权。能直接访问受信运行时接口的调用方也能提交本操作。外部连接 URL/headers 仍由可信解析器提供，不接受请求指定目标。

Backend 与 Engine 请求合同同时变更，旧签名字段不再接受，需要协调发布双方。现有日志保留，不记录连接凭据。测试只替代外部发现和 HTTP I/O，文件操作与身份校验运行真实实现。

## 本地验证结果

- RED：无 authorization 请求在原实现返回 422，未产生文件写入。
- GREEN：Engine 定向 45 passed；Backend 定向与合同 59 passed。
- Backend 全量：18,795 passed、43 skipped；Engine 全量：2,709 passed、5 skipped。测试有依赖弃用/资源警告，无失败。
- 修改文件的阻断级 flake8、未使用导入/变量检查及 git diff --check 均通过；修改的 Python 文件均小于 1,000 行。
- 本次未计算变更行覆盖率，未执行远端 CI、提交推送或部署；预发仍须发布新版 Backend/Engine 后再做接口实测。
