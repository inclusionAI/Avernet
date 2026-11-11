# Provider AgentCard 默认负责人

## 范围

Provider 创建/更新支持 `a2a.default_owner`，存储在既有 Provider config JSON，不新增表、索引或迁移。仅供公共 `POST /a2a/agents` 在 owners 省略、null、[] 时使用；普通 Bot 注册入口不变。真实部署需包含本改动。

## 创建

`POST /providers`，沿用现有已认证 Human 身份要求。示例 JSON（身份认证由部署现有方式提供）：

```json
{
  "name": "Travel Provider",
  "slug": "travel-provider",
  "auth": {"mode": "static_bearer"},
  "a2a": {"default_owner": "410025"}
}
```

工号只是示例，不在代码中硬编码。创建省略 a2a 时不配置默认负责人。

## 更新已有 Provider

```http
PATCH /providers/{provider_id}
Authorization: Bearer <provider_admin_token>
Content-Type: application/json
```

同时须满足原有已认证 Human/Provider owner 权限校验；仅有管理 token 不意味着可以绕过 Human owner 校验。

```json
{"a2a":{"default_owner":"410025"}}
```

- 省略 a2a、a2a:null 或 a2a:{}：不触发 A2A 配置合并或配置回写；同请求其他配置字段的更新照常保存。仅改名时不会恢复被并发请求清除的默认负责人。
- default_owner 为合法字符串：设置/替换；1～128 UTF-8 字节，无首尾空白、控制字符或 human_ 前缀。不验证员工目录存在性，沿用原有 raw user ID 语义。
- default_owner:null：清除默认负责人。其他 A2A/Provider 配置保留。
- 空字符串、非法类型、非法 ID、未知 A2A 请求字段：拒绝，不连带提交同请求其他元数据。
- 清除默认后，省略 owners 的 AgentCard 注册返回 422 INVALID_OWNER；显式合法 owners 仍可注册。

## 回读

使用原有 Provider 管理凭据 `GET /providers/{provider_id}`，响应增加：

```json
{"a2a":{"default_owner":"410025"}}
```

未配置的旧 Provider 可不返回 a2a；清除后 default_owner 为 null。无鉴权的 Provider slug 基本信息接口不暴露此配置。

## AgentCard 注册与权限边界

- owners 非空时使用显式唯一 owner；默认值不会覆盖它。
- 默认值只初始化新 Bot 的 owner，不增加 manager、不修改已有 Bot owner、不联动 user_visibility。
- 配置从已认证 Provider 读取，不能由注册请求指定另一个 Provider 的默认负责人。
- 缺失/非法默认值在 Card URL 拉取前拒绝，不写 Bot/Card/ownership。
- 现有 Provider 配置写入仍沿用原先读改写和授权方式；本变更不引入新的并发控制承诺。注册使用认证时读取的配置快照，后续配置变化只影响之后的注册。

## 实现与成本

HTTP typed patch → Provider application → Provider Core：创建在 insert 前合入 config；更新在一次现有 metadata write 前局部合并并校验。不额外增加 DB round trip 或网络调用；配置回读不返回其他私有配置。公共 AgentCard 注册复用已认证 Provider config，不增加额外配置查询。

回归覆盖见 `provider_slug_contract::a2a_default_owner_create_patch_readback_and_permissions`、`public_management::provider_config_update_changes_only_future_default_owners`、`provider_a2a::tests`。`bcs-bot/tests/provider_a2a_patch.rs` 固定改名读取、并发清除、改名写入的交错顺序，验证空补丁不会恢复旧默认值，并覆盖同请求更新其他配置的场景。
