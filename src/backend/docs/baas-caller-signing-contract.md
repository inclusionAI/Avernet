# BaaS → Backend JWT 签发与验签对接规范

适用接口：`POST /api/v1/expert-chats/app-caller-connection`。

更新时间：2026-09-16。

**状态：本分支已实现，部署后生效。** 仅适用于下述 BaaS 服务入口。

## 1. 鉴权范围

此接口认证调用方为受信任的 BaaS 服务：

- 验证 HS256 签名。
- 要求 issuer 严格等于 `baas`。
- 要求并验证 `iat`、`exp`；携带 `nbf` 时也校验生效时间。
- 不要求、不解析、不校验 `principals`。
- 不要求、不校验 `app_id` 或 `tenant`。
- 不做用户登录鉴权，也不检查应用grant或Caller成员权限。
- 延续本接口普通HTTP策略，不校验audience；签发方可保留 `aud=backend` 作为用途说明。

请求中即使携带principals、app_id或tenant，也不将它们用于鉴权或租户路由。Backend使用该服务入口既有的服务端租户上下文/配置访问数据，不从未校验的请求字段切换租户。“不校验tenant”不等于新增跨所有租户扫描或查询能力。

签名证明调用来自持有受信任密钥的签发方，不证明某个终端用户或独立应用的身份。保留Bot存在和发布产物等业务检查；允许首次创建Caller实例及容器。

## 2. HTTP传输

```http
X-Avernet-Principal: <完整JWT>
```

调用链：BaaS生成JWT → 请求Backend → Backend按BaaS服务协议验签 → 进入Caller创建、复用或升级流程。

- Header直接传原始JWT，不加 `Bearer `。
- 不将整个JWT再次Base64或URL编码，不传JSON对象或单独的签名段。
- 不把应用API Key、IAM token、Caller连接token当作JWT。
- 修改JWT任意字段后必须重新签名，不能复用旧signature。

## 3. Header与Payload

Header：

```json
{
  "alg": "HS256",
  "typ": "JWT"
}
```

- `alg` 必须为HS256，不接受none、RS256、HS512。
- `typ=JWT` 为推荐格式，不新增typ字段值校验。
- `kid` 不必传，不新增基于kid选密钥的行为。

Payload结构示例：

```json
{
  "iss": "baas",
  "aud": "backend",
  "iat": 1789541400,
  "exp": 1789541460
}
```

示例时间只展示秒级时间戳格式，实际请求必须使用当前时间生成。

| 字段 | 类型 | 必填 | 目标校验规则 |
|---|---|---|---|
| iss | string | 是 | 必须严格等于baas |
| iat | integer | 是 | 签发时刻，Unix秒；不能使用毫秒时间戳 |
| exp | integer | 是 | 过期时刻，Unix秒；签发端必须保证exp > iat |
| nbf | integer | 否 | 发送时校验尚未生效条件，无需求可省略 |
| aud | string | 否 | 建议backend，但此入口不校验 |
| principals | 任意/不传 | 否 | 不参与解析、鉴权或租户路由，建议省略 |
| app_id | 任意/不传 | 否 | 不校验、不作为身份依据，建议省略 |
| tenant | 任意/不传 | 否 | 不校验、不用于切换数据租户，建议省略 |

## 4. 时间策略

- `iat = floor(当前UTC时间的Unix秒数)`。
- `exp = iat + ttl_seconds`。
- 建议BaaS按请求签发，初始TTL可采用60秒；60秒是接入建议，不是约定的Backend最大TTL校验。
- 保留现有验证器的5秒时钟容差，用于小幅时钟偏差。
- 过期、未来iat、未来nbf超出容差时拒绝。
- 两端同步时钟，不长期缓存JWT，不复制文档中的固定时间上线。

## 5. 签名计算与密钥约定

JWT由三个无padding的Base64URL片段组成：

```text
encoded_header  = Base64URL_NoPadding(UTF8(JSON(header)))
encoded_payload = Base64URL_NoPadding(UTF8(JSON(payload)))
signing_input   = ASCII(encoded_header + "." + encoded_payload)
signature_bytes = HMAC_SHA256(key_bytes, signing_input)
encoded_sig     = Base64URL_NoPadding(signature_bytes)
jwt             = encoded_header + "." + encoded_payload + "." + encoded_sig
```

- Base64URL使用 `-`、`_`，去掉末尾 `=`。
- HMAC输出必须使用原始32字节结果；不要先转hex字符串再编码。
- 签名覆盖最终发送的Header和Payload两段，包含中间的点。
- 不用普通SHA256代替HMAC，不将secret直接拼接进签名输入。
- JSON字段顺序不作限制，但签名后不得重新序列化或修改内容。
- 优先使用已有JWT库的HS256签发功能。

BaaS与Backend必须通过受控配置加载相同密钥字节，禁止将密钥随请求发送。双方约定同一种密钥编码，不在一端额外Base64解码、hex解码或添加换行；新密钥应具有至少32随机字节的强度。

复用 Backend 启动时通过 `SecretNamesConfig.gateway_principal_signing_key` 加载的现有密钥，不增加独立 BaaS 密钥或配置项。BaaS 签发进程必须通过受控配置加载相同值；本变更不传输或轮换密钥。共享密钥的持有者之间没有密码学隔离，issuer 仅区分协议。轮换后应确认双方运行进程已加载新值。

不要记录完整JWT或密钥；排查时比较受控的密钥指纹及长度。

## 6. Backend目标处理流程

```text
读取非空X-Avernet-Principal
  → 按受信任配置验证HS256签名
  → 校验iss=baas、必填iat/exp及可选nbf
  → 不解析principals，不校验app_id/tenant/aud
  → 使用既有服务端数据上下文
  → 检查Bot并进入Caller创建、复用或升级流程
  → 执行连接业务
```

此协议仅作用于该BaaS入口，不能修改共享验证器默认issuer或取消其他接口的Principal身份校验。

鉴权失败沿用HTTP401：

```json
{"detail":"Unauthorized"}
```

Bot不存在属于业务失败，与签名失败分开处理。实例不存在或没有bot_uuid时进入底层创建流程，仍需满足发布产物等生命周期条件。既有接口可能返回HTTP200并携带 `success=false, error_code=403`，调用方不能只看HTTP200。

## 7. 验收要求

| 用例 | 预期 |
|---|---|
| 正确签名、iss=baas、有效iat/exp，不传principals/app_id/tenant | 通过鉴权；完整成功还需有效业务实例 |
| 额外携带任意principals/app_id/tenant，其他验签项合法 | 不因这些字段拒绝，不用这些字段改变身份或租户路由 |
| 改变aud或不传aud，其他验签项合法 | 不因aud拒绝 |
| 缺Header、Header为空、JWT缺段或额外加Bearer前缀 | 401 |
| 错误密钥、错误算法、未签名或签名后改Payload | 401 |
| iss=gateway、iss缺失或其他值 | 此BaaS协议401 |
| 缺iat或exp、已过期或未来iat/nbf超出容差 | 401 |
| 鉴权成功但实例不存在或没有bot_uuid | 进入底层实例/容器创建流程 |
| 其他使用网关Principal的接口 | 原有issuer/身份/租户验证行为不变 |

## 8. 实现范围

本入口以配置副本设置 `issuer="baas", verify_audience=False`，复用 `decode_principal_token` 后丢弃 claims。中间件按内部服务入口使用默认租户，Service API 只接收业务目标参数。共享验证器、ordinary HTTP org 与 OpenAPI 的身份和租户策略保持不变。
