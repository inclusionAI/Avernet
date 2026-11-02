# Provider 管理入口注册 A2A Bot

更新：2026-10-31。沿用 `POST /providers/{provider_id}/bots` 与 Provider Admin
Bearer，支持两个互斥来源：`agent_card_url` 或完整 `agent_card` 对象。

```json
{
  "owners": ["<owner-staff-id>"],
  "provider_bot_ref": "order-agent",
  "agent_card_url": "https://agent.example.org/a2a/bots/order-agent/agent-card.json"
}
```

也可以将 URL 字段替换为 `"agent_card": {完整的标准 AgentCard}`。

## 选择、默认值与兼容

- 两种来源都缺省/null：原 Provider Bot 注册路径，名称及其他原有要求不变。
- 两者同时提供、URL 空/带外围空白、Card 非对象：400，不发起 Card 获取。
- A2A 必须 gateway 且无 Bot webhook_url；Provider 也无需默认 webhook。
- A2A name 可省略/为空，默认 Card.name；显式值为 BCS 展示别名。既有名称
  2..64 字符规则不变，不能静默截断 Card。summary 未提供时默认 Card.description。
  默认值只初始化，不在刷新时覆盖现有别名。skills 与未知字段保存完整 Card。
- provider_bot_ref、单个 owner、Provider 授权、归属、可见性和权限不从 Card 推导。
- GET/PUT `.../bots/{bot_uuid}/a2a` 读/更新最小 Card 配置；PUT 使用同样互斥来源
  与 expected_revision。刷新端点仅重读已保存 URL，inline Card 需要显式 PUT 更新。

## 授权与原子性

先验证管理凭据与启用状态，再拉取 URL 或校验 inline 内容；两条路径使用同一
官方 SDK 声明验证和出站策略，不执行探测 Task、不把 Provider 凭据发给 Card host。
Core 在异步校验后重新验证 Provider/凭据/注册准入。一个事务创建 Bot、Provider/ref、
Human/owner、profile、权限审计与 Card。审计主体为实际 Provider，支持明确委托 Bot owner。
失败不返回 runtime token、不留下半注册行。JSON 中的 provider/owner 声明不授予 BCS 权限。

重复、墓碑与并发同 ref：409，不覆盖 Card、不重发已有凭据。更新使用独立 CAS 入口。
Provider AgentPass/子 Bot 身份及凭据返回规则保持既有行为。

## 存储与错误

bcs_bots 仅保留 agent_card、可空 agent_card_url、agent_card_revision 三个 A2A 列。
接口按当前 Card/部署策略派生；凭据按 Provider 绑定与固定下行类型获取。
初次接口不兼容、必需扩展不支持、非法鉴权声明、出站地址拒绝或失败事务均返回错误。
周期/手动刷新失败保留最后成功快照，不持久化错误状态；在途运行保持冻结信息。

详细协议、scoped `/register` JSON 模式、分页与 SQL 成本见
[minimal-card-registration-v1.md](minimal-card-registration-v1.md)。验证记录以该改造的
最新测试结果为准，早期 URL-only 注册的 54 项记录属于历史阶段。
