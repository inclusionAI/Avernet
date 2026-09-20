# BCS Bot 独立 webhook 地址开发规格

- 日期：2026-09-20
- 状态：开发方案草案；尚未实现
- 所属模块：BCS
- 实施步骤：[plan.md](plan.md)

## 1. 目标与范围

同一个 Provider 下的 HTTP Bot 可以分别使用不同的下行 webhook 地址。
保留 Provider 级公共地址作为默认值，兼容现有接入方式。

已确认的需求：

- Poolab 每个 Bot 的地址创建后基本固定。
- 注册接口由实际 Provider 接入方使用，本次不涉及 Backend 注册链路。
- BCS 保存接入方提供的地址，不调用 Poolab 管理接口获取或拼装地址。

本次交付包括 BCS 的注册、查询、地址更新、持久化、目标解析、协议文档及测试。
Provider 接入方负责取得兼容的 webhook 地址并提交给 BCS。
动态服务发现、运行迁移、每 Bot 独立鉴权、任意请求头配置不在本次范围内。

这里的 webhook 指 BCS → Provider/Bot 下行入口，不是 `/bot/events` 上行回调，
也不是组织管理通知的 `admin_callback_url`。

## 2. 当前实现与设计依据

| 当前实现 | 代码位置（相对仓库根） | 改造含义 |
| --- | --- | --- |
| Provider 保存统一 webhook | `src/bcs/crates/services/bcs-bot/src/core/provider_core.rs` | 公共地址调整为可选默认值 |
| Bot 绑定只有身份、禁用状态及时间 | `src/bcs/crates/contracts/bcs-domain/src/provider.rs` | 新增绑定级 URL |
| 目标解析始终取 Provider URL | `src/bcs/crates/services/bcs-bot/src/core/bot_core.rs` | 在此统一确定实际地址 |
| HTTP 投递已接收具体 URL | `src/bcs/crates/adapters/http/bcs-provider-http/src/lib.rs` | 复用现有发送实现 |
| 同 ref 重复注册直接返回旧绑定 | `src/bcs/crates/services/bcs-bot/src/core/provider_core.rs` | 不将注册偷偷改成更新 |
| DB binding 查询具有 30 秒进程内缓存 | `src/bcs/crates/services/bcs-bot-store/src/provider.rs` | 更新后本地失效，明确多副本语义 |

遵循以下既有规范：

- [架构规则](../../../../docs/arch/arch.rules.md)
- [CI 规则](../../../../docs/arch/ci.enforce.md)
- [BCS 分层规则](../../CLAUDE.md)
- [Provider 接入协议](../../../../docs/bot-provider-integration.zh-CN.md)
- [Provider 2.0 协议](../../docs/bcs-provider-2.0-sse-protocol.md)

当前系统 ADR 目录没有直接约束 Provider webhook 地址粒度的 ADR。
本次沿用既有 Provider/Binding/DeliveryTarget 边界，不新增 Poolab 专属领域分支。
`CLAUDE.md` 中早期“仅 WebSocket”的叙述与已存在的 HTTP Provider 契约不一致；
此次以当前 Provider 接入契约和已实现的 Gateway/Plugin 分流为设计依据，实施时同步校正文档。

## 3. 模型与不变量

### 3.1 配置归属

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `Provider.config.downlink.webhook_url` | 可选字符串 | Provider 公共默认地址；旧字段名不变 |
| `ProviderBotBinding.webhook_url` | 可选字符串 | Bot 独立地址；空值表示继承 |
| `BotDeliveryTarget::HttpProvider.webhook_url` | 必填字符串 | 已解析、可用于投递的最终地址 |

绑定模型新增 `#[serde(default)]`，使旧序列化数据能够读为 `None`。
数据库 `bcs_provider_bot_bindings` 新增 nullable `webhook_url` 文本列，默认 NULL。
不把 Provider 默认地址复制到每个 Bot，不增加 URL 索引，不改变已有身份唯一约束。

### 3.2 解析顺序

```text
Bot 独立地址存在 → 使用该地址
否则 Provider 默认地址存在 → 使用默认地址
否则 → 返回 missing_delivery_endpoint 配置错误
```

该规则仅决定地址，不绕过 Bot/Provider 禁用状态、凭证检查、协议版本与 URL 校验。
明确配置的 Bot 地址无效或不可达时直接返回对应失败，不能回退到公共地址重投。
无 Provider 绑定的 Bot 仍走 WebSocket；有绑定但缺地址不能退回 WebSocket。

Provider 的配置解析负责校验“提供的值是否有效”；是否存在最终可投递地址，
在建立 HTTP 绑定及 `resolve_delivery_target` 时检查。只查询 auth mode 的调用点
不再因为 Provider 没有公共地址而失败。

## 4. 管理接口契约

这些是 Provider 管理 Service API 的 HTTP 表达；下行 webhook 报文仍沿用现有协议。

### 4.1 Provider 注册与查询

- `POST /providers` 的 `webhook_url` 改为可选；缺省或 null 表示没有公共地址。
- 非空字符串继续按现有出站 URL 策略验证；空串、纯空白及非法类型均拒绝。
- Provider 查询中，有默认地址仍返回原来的字符串；没有默认地址返回 null。
- Provider 仍保留 auth mode、protocol version 和下行凭证，不能用缺地址表示禁用。
- 一期保留现有 Provider PATCH 的 null/缺省均不修改语义，不增加清除公共地址能力。
  原先没有默认地址的 Provider 可以通过已有 PATCH 设置一个有效默认地址。

Provider 查询字段可为 null 是管理契约的类型扩展，相关 DTO/客户端需要适配。
旧 Provider 的返回值不变，但不能声称所有旧客户端天然兼容新建的无公共地址 Provider。

### 4.2 Bot 注册

`POST /providers/{provider_id}/bots` 新增可选 `webhook_url`：

```json
{
  "name": "poolab-bot-a",
  "owners": ["owner-id"],
  "provider_bot_ref": "bot-a",
  "webhook_url": "https://bot-a.example.com/bcn/webhook"
}
```

- Gateway：缺省/null 表示继承默认地址；两级均缺地址时，在写入 Bot 或绑定前拒绝。
- Plugin：不建立 HTTP 绑定；拒绝非 null 的 webhook 配置，避免返回成功却忽略配置。
- 返回值和已鉴权的 Provider Bot 列表增加 `webhook_url`，表示绑定配置值，null 表示继承。
- 不向无鉴权的 Bot 发现接口扩散 endpoint 配置。

重复注册继续以 `(provider_id, provider_bot_ref)` 为身份：

- 未提交非 null URL：保持旧幂等行为，返回已有绑定及当前配置。
- 提交 URL 与已保存的 Bot 覆盖值相同：幂等成功。
- 提交不同 URL，包括原来继承、现在请求显式覆盖：返回 409，提示使用更新接口。
- 比较保存的配置值，不用 DNS 结果或任意 URL 重写判断是否相同。

### 4.3 Bot 地址更新

扩展已有 `PATCH /providers/{provider_id}/bots/{provider_bot_ref}`。
当前 router 占位符名是 `bot_uuid`，实际 handler 按 `provider_bot_ref` 查询；
保持当前查找语义，并在契约文档里明确，避免无关的身份迁移。

| webhook 输入 | 结果 |
| --- | --- |
| 缺省 | 地址不变，旧能力更新请求保持原语义 |
| 非空 URL | 设置或替换独立地址 |
| null | 清除独立地址；没有 Provider 默认地址时拒绝 |
| 空串、空白、非法 URL/类型 | 400，保存值不变 |

HTTP 层使用自定义字段存在性反序列化，将缺省、null、值映射成
`Unchanged / Inherit / Set(String)`，不能用普通 `Option<String>` 丢失三态。

一期地址修改必须单独提交，不与 name/summary/domains/skills/scopes/visibility 混用。
混合请求在任何持久化前返回 400；旧能力 PATCH 不受影响。
原因是现有能力与绑定由不同 Repo 更新，当前没有统一事务业务端口；
本次通过一个请求只更新一个存储对象，避免新增跨存储事务或部分成功语义。
此约束是本方案的实现选择，不是现有 API 已有行为。

只有已鉴权且属于该 Provider 的绑定能被修改。地址更新分支不重写 Bot capabilities。
写入失败返回错误，不能先改缓存再返回成功。成功响应返回已保存的绑定 URL。

### 4.4 其他建绑定入口

WS → Provider 切换入口也会创建绑定，必须保持有效地址不变量：

- 一期不为该入口扩展独立地址参数。
- 新建绑定时继承默认地址，没有默认地址则在改 Bot 状态或断开 WS 前拒绝。
- 已有同 Provider/ref 绑定的幂等请求，按其已保存的覆盖地址或默认地址验证。
- 不能因放宽 `parse_downlink_config` 而允许创建没有投递地址的 HTTP 绑定。

## 5. 持久化与投递边界

Memory 和 DB store 都新增绑定地址更新方法，并覆盖所有单条、按 ref、批量、列表读取。
DB 更新按 env、provider_id、bot_uuid 定位；旧数据为 NULL 时正常继承。
SQLite 使用版本化迁移并测试旧库升级和重复启动；MySQL 增加增量迁移。
已有迁移有 checksum，不修改已发布的迁移内容来代替增量迁移。

所有新的下行请求继续通过统一 `resolve_delivery_target` 取得目标，覆盖 send、inject、
history、abort、interaction 等已有操作。发送层继续使用现有出站 URL guard，
保留请求时 DNS/IP 校验、重定向策略、日志脱敏及已有 1.0/2.0 处理。

绑定更新提交后，失效本实例按 Bot/env 索引的缓存。多副本仍为现有 TTL 的最终一致，
不承诺管理请求成功后其他实例立即读到新地址。
本期地址修正应在停止新投递、等待旧运行结束后进行；恢复投递前确认各实例已解析到新值。
已经发送的请求/SSE 流不会迁移，已有取消逻辑仍会解析当前地址。
本期不新增路由版本、运行级 endpoint 快照或自动重投机制。

## 6. Provider 接入方责任

1. 注册一次 Provider，可省略公共 URL。
2. 创建 Bot 并取得创建后稳定的 webhook 地址。
3. 注册 Bot，同时提交 provider_bot_ref 与 webhook_url。
4. 接收端实现既有 Provider webhook 请求/响应协议，并校验下行凭证。
5. 发生运维修正时调用独立的地址 PATCH 请求，不依赖重复注册更新地址。

Poolab 原始设备访问接口可能另需 token/target。上述“只改 URL”的交付以接收端兼容
现有 BCN webhook 及 Provider 下行鉴权为前提。若不兼容，由 Provider 接入适配层处理；
不在 BCS Core 内引入 Poolab SDK、平台 token 或专属请求头逻辑。
这项协议兼容性尚未进行真实 Poolab 联调，应作为接入验收项记录。

## 7. 兼容性与发布

发布顺序：增量迁移 → 全部 BCS 实例升级 → Provider 接入方启用新字段。
混合版本期间不启用新配置：旧实例可能忽略字段或继续投递公共地址。

旧请求不带新字段时保持原行为；旧绑定 NULL 继承；下行报文版本号不因地址选择而升级。
Service API 参数、Repo 接口、DTO 和 Rust struct literal 的所有实现/调用方必须同步编译检查。

回滚时保留新增 nullable 列。已经启用独立地址或无默认地址 Provider 后，不能直接回滚到
忽略 Bot URL 的旧二进制；应先停止相关投递，并恢复能正确路由的兼容公共入口，
或保持受影响接入停用直到重新部署支持版本。

## 8. 验收标准

- 同一 Provider 下 A/B 两个 Bot 分别只命中 A/B 两个接收端，携带正确 provider_bot_ref。
- 同一 Provider 下旧 Bot 仍命中默认入口，覆盖地址的 Bot 不受默认地址修改影响。
- 无公共地址 Provider 可以创建；其 Gateway Bot 必须提供独立地址。
- 两级均缺地址、非法 URL、跨 Provider 更新失败且无部分写入。
- 覆盖地址失败不会调用默认入口；禁用 Provider/绑定/凭证仍阻止投递。
- 注册幂等、地址冲突、PATCH 三态和混合 PATCH 拒绝均符合契约。
- Memory/SQLite 持久化回读一致，旧 SQLite 库升级/重复启动通过；MySQL 迁移经验证。
- 地址更新后本实例缓存失效；多实例的 TTL 行为与文档一致。
- 1.0 callback、2.0 SSE，以及 inject/history/abort/interaction 等既有方法无地址选择分叉。
- 无绑定 WS Bot 与受限的 WS → Provider 切换路径回归通过。

## 9. 实施与验证记录

实现位于 `codex/bcs-bot-webhook`，基于最新抓取的 origin/dev
`e8cdf07b2057438c44668e437e88950d4bb2480c`。本次只改 BCS 及其接入文档。
按用户实施阶段的明确要求，暂不处理已有文件的 1000 行限制，未拆分文件。

功能、迁移、兼容性和真实 HTTP 接收端测试已实现。
具体执行结果与未验证项见 [validation.md](validation.md)。
