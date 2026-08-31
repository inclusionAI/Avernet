# W3 — 租户级源凭证(#1471)

> 计划来源:执行计划 Phase C;设计文档 §4.3。归属:lucas-xzp(实现项)。依赖:—(W2 的
> 协议接口用结构化满足,不 import——谁后合并谁接线)。
> 验收:issue #1471 为唯一权威。

## 交付物

- `core/bot_config_manifest/credentials/`:errors / models(Row 存储形 + masked 公开形,
  值从构造上就进不了公开形)/ policy(前缀段边界 + 解码后规范)/ service(+ service_protocol)
  + W2 绑定(`SourceCredentialBinding`:`headers_for`/`reauthorize`,每跳现读)
- 仓储协议/实现(`core/repository/{protocols,implementations}/bot/source_credential.py`)
- `ac_source_credential` DDL(uk `(avernet_tenant, name)`,无 env 轴)
- DI:provider 按 profile 定 fail-closed(corp/community=True)
- 公开面 4 路由(`/openapi/v1/source-credentials`,REFUSED 全组)+ ADMISSION/AUTHORIZATION
  + gateway 新前缀 spec 文件 + 本仓 `application.yaml` 一行

## 验收 → 实现/测试映射

| issue 验收 | 落点 | 测试 |
| --- | --- | --- |
| 租户级表 (tenant, name) | uk 无 env 轴(DDL 注释陈情同样的-Key 推理) | repo/adapter 往返 |
| TokenVault 可逆加密 + master key 来源 | `TokenVault` 复用 + `has_master_key` | 密文断言 |
| fail-closed 生产守卫 | DI provider 按 profile 注入;写入前拒绝 | 503 + 空库断言 |
| allowed_prefixes 必填 https 绝对前缀 | `validate_prefixes` | policy 参数化 |
| 段边界匹配 | 解码+点段规范后再比 | content vs content-secret |
| 前缀外 → 条目 failed(绝不裸连) | binding `reauthorize` raise | adapter 拒测 |
| 跨前缀重定向失败 | W2 fetcher 每跳调 reauthorize(duck) | 联测留 W2 合并后 |
| GET 只回脱敏元数据 | masked Record 构造性排除值 | adapter 断言 |
| oss_aksk/basic 写入即拒 | service + errors 映射 422 | 参数化 |
| 401/403 报"凭证 <name> 被拒" | 绑定 surface `credential_name`;条文归 W4 | spec 记债 |
| 轮换 = 重 PUT,不触发 apply | upsert 同名替换;绑定每跳现读 | rotation 两测 |
| 删除被引用 → 下次 apply 具名失败 | delete 放行;binding 具名 404 | del-binding |
| 值不出现于日志/错误/报告 | 名字-only 消息形态 | message 断言 |

## 执行记录(2026-08-31)

- 收编了另一会话的部分草稿(存储/策略骨架),按设计规范重写:Protocol 落位 core(owning
  module)+ api re-export、去 `production` 形参(DI fail_closed)、去 env 轴(uk 与过滤
  同构)、Row 记录脱离 session 交接、错误家族统一 `CredentialError`、错误映射 dict
  **子类排在基类前**(isinstance 首个命中是条顺序敏感的坑)。
- 全绿:policy 24 / service 23 / adapter 9 / endpoint-framework 9 / 门禁 battery
  (conformance、E3、oversized allowlist、schema-docs、admission-inventory、principal-seam、
  coverage gate 16)。
