# Manifest MCP 配置协议评审

状态：本轮 Q1-Q13 核心协议及内部行为已确认，并已进入实现；本文保留评审时的事实基线与决策记录。

面向业务评审的整合版本：[一期协议评审稿](2026-09-21-manifest-mcp-protocol-draft.md)。本文件保留事实核查与决策记录。

内部落地设计：[模块、存储与实施顺序](2026-09-21-manifest-mcp-internal-design.md)。已确认的分表与事务取舍记录在 [ADR 0014](../../adr/0014-separate-bot-mcp-overrides-from-user-config.md)。

## 目标与已有约束

通过 Manifest 为一个 Bot 声明 MCP 及其连接配置；同一 MCP 在不同 Bot 上可以有不同 URL、Header。先由产品、涔涔及业务方确认外部合同，再评审内部实现。

沿用此前确认的 Runtime best-effort 投影语义，与 Skills 保持一致；不以此扩展交付保证。但协议仍须区分配置接受、持久化成功和运行时可用。

当前代码基线按用户要求改为 dev，已 fetch：Avernet origin/dev `2c943c8f6fa929d02337cd3b26d0ec74fb5cfd6d`；OCB origin/dev `b04a9348e8dcbfe1018ccc9fd0f76e6fb05c633e`，其 ocb-public gitlink 为 `ded8c971ef380aba6676916d51f2f2cd3baf9bbf`。两个公共代码提交不同；已检查的 Manifest/MCP 核心目录及 mcp_device_payload 在二者间没有差异，不等于整个企业系统兼容已验证。通过 git show 只读检查，未切换当前 dev_stable 工作目录。历史研究基线为 OCB `dd2957edb8` / public `574c01e2d1f9a59e99ad802872a6d0845f85d915`；均不代表部署验证。

评审基线中的 MCP entry 仅接受 `server_code`；本次实现以可选 `config` 扩展
schema v1，并保持旧文档兼容。

已核实的集合语义：`apply/orchestrator.py:75-80` 中省略 mcp 不触碰该集合，`mcp: []` 清空其管理集合。`apply/materialisers/mcp.py:221-240` 对已安装集合扣除 platform defaults 后做全量差异；其中包含 UI 安装项，不只管理此前由 Manifest 写入的项。协议扩展应明确继承该语义，不把 config 字段合并误解成 MCP 集合增量添加。

## 对外必须确认

- 资源身份（已确认）：沿用现状，仅支持 MCP Center 已登记的 server_code。
- 作用域：配置属于目标 Bot，不应修改该用户其他 Bot。
- 配置能力（核心语义已确认）：URL、非敏感 Header、endpoint_env、transport_protocol；显式 URL 替换基础端点地址，协议仍由 Center/用户配置或显式选择决定。
- 继承与更新：未填写、空对象、清空、删除、重复提交的语义；Bot 配置与 user default 的优先级。
- 验证与反馈：未知字段、非法 URL、MCP 权限不足、运行时下推失败如何返回。
- 兼容：仅含 server_code 的旧 Manifest 如何继续生效；保持已有 apply 模式和集合语义，需复核细节。

## 第一轮决策（已确认）

1. 沿用现状：所有可安装 MCP 都必须已在 MCP Center 登记，以 server_code 标识；Manifest 可以为 Bot 新增该 MCP 的 installation，不在 Manifest 中注册中心之外的新 MCP。
2. Manifest 显式提供的 Bot 配置优先于 user config；未提供的字段沿用既有来源。headers 作为一个完整字段处理：提供 headers 时整组替代 user config 的 headers，不逐 key 合并；未提供时沿用用户配置。该规则不等于替换容器中的平台 headerPolicies。现有 user config 不支持自定义 URL，URL 未覆盖时仍来自 MCP Center。重复 apply 与清空规则已在下文确认。
3. 已确认：一期 Header 使用明文字面值，业务方确认一期不包含敏感 Header。凭据引用、Mist/SecretResolver 接入及统一数据安全方案留到后续，不作为一期前置。
4. 已确认：Manifest 支持 endpoint_env、transport_protocol；后端根据 server_code 查询 MCP Center 元数据校验所选端点。保持与 user config 相同的字段含义和取值体系，不只做枚举校验。Manifest 显式选择不匹配时返回校验错误，不静默回退其他协议。应在最终有效环境/协议组合下校验同一个可用端点，而非两个字段各自曾出现在不同端点就通过；具体错误码和定位格式尚待细化。该决定不自动修改旧 user config API 的兼容行为。

澄清：user config 指当前 ac_user_mcp_config 中用户为某个 MCP 保存的配置。Bot 配置优先级是本次新增合同，不是既有 Manifest 能力，也不是 mcporter 的 headerPolicies。

## 协议整合

已整理独立一期协议评审稿，包含 YAML、字段表、更新/清空示例、错误含义、兼容说明及验收场景。一期不引入 secretRef；新增能力未实现，版本与返回结构等剩余细节在评审稿中列明。

## 内部改造范围

- Schema/parser/validation：解析协议并拒绝不支持字段。
- Plan/materialiser：比较配置差异，并与 Bot MCP 安装关系一同持久化。
- Persistence（Q10 已确认）：保留用户配置表，新建 Bot 配置表，只存显式覆盖。
- Effective config resolution：汇总中心元数据、用户配置与 Bot 配置，按上述已确认的字段优先级解析；新增凭据引用解析不在一期范围。
- Projection：配置变化也触发同步；维护连接配置与允许集合的顺序。
- 生命周期：上线、重启及其他重投影入口使用同一配置解析规则。
- 企业装配和运行时：OCB 的凭据解析、Header policy 与各 Engine/provider 行为保持一致；需要改 runtime 时纳入镜像版本兼容。

## 下一轮分支

### Q7-Q9 已确认

- Q7 Header 冲突：Manifest 声明同名平台托管 Header 时拒绝并定位字段（大小写不敏感），不将配置接受后静默替换；普通 Header 保持已确认的整组覆盖用户配置。动态 headerPolicies 的保留范围需要在实现设计中明确，不凭名称认定任意 Header 都属于平台。
- Q8 远程配置的输入范围：URL 支持绝对 http/https；Header 使用字符串键值，拒绝非法换行和大小写重复键。Center 的 LOCAL/stdio MCP 仍可仅凭 server_code 安装，但拒绝这次新增的远程 config 字段。具体目标网络/域名规则沿用平台访问边界。URL userinfo/fragment 的进一步限制此前只在笔记中建议，未单独确认。
- Q9 校验时机与失败范围：保存时验证形状，apply/dry-run 解析时检查 Center 端点组合及权限；可预检错误导致本次整个 mcp 类目不写入，其他类目按已有独立规则执行。持久化途中异常仍如实报告部分完成，运行时 best-effort 保持已确认语义，不增加全 Manifest 原子性承诺。保存成功不表示 Center 校验通过。现有 apply entry 报告只有 name/action/error/note，字段级结构化错误需评估最小兼容扩展。

### Q10 / Q11 已确认

- Q10 存储边界（已确认）：保留用户表，新建 Bot 覆盖表；只存显式覆盖，不物化继承值。未选择扩展旧表增加 scope/bot_id：该方案需同时调整唯一约束及所有用户级查询/更新/删除过滤，简单 nullable bot_id 也不能自动保证默认行唯一。新增表仍需接入逐 Bot 的有效配置解析，不能只改存储。
- Q11 继承漂移（已确认）：用户后来修改未被 Bot 覆盖的字段时，重新解析每个 Bot。如果本次修改引入与 Bot 显式协议/环境不兼容的新组合，拒绝该次用户更新，保留旧用户配置，返回冲突 Bot 与原因。检查在持久化候选用户配置及向设备推送之前完成；不回退协议、不删除 Bot 覆盖。运行时同步失败仍保留旧 API 合同，不扩展为全局 best-effort 用户写入。

Q11 澄清：冲突不是 Bot 与用户同字段取值不同（此时 Bot 覆盖正常生效），而是使用“候选用户配置 + Bot 显式覆盖”解析后的环境/协议组合不存在于 Center 可用端点。检测先按旧用户更新 API 的 None=保留语义构造候选值，再逐 Bot 应用已确认优先级，复用 Manifest 的 Center 端点校验。应区分旧配置已失效与本次候选变更引入的新冲突，Center 不可用也不能冒充组合冲突。

### Q12 / Q13 已确认

- Q12 单个 MCP 持久化：同一个 Bot/MCP 的安装关系及覆盖配置在同一数据库事务内新增/修改/删除，提交后才投影。某条持久化失败不能留下该条半套状态；不同 MCP 之间仍保留现有类别 write 部分完成语义，不扩展成全 Manifest 事务。
- Q13 已保存配置后续失效：Center 下架协议或更改端点导致启动/重投影无法解析时，保留数据库期望配置，报告该 MCP 不可投影，不自动清除覆盖或回退协议。与瞬时投影失败一样，不主动删除设备既有配置或将其报告成已同步；新实例没有可用配置就保持该 MCP 不可用。Center 恢复后由后续既有同步/重投影入口再次处理，不承诺立即自动恢复。不改变权限撤销的独立安全策略，不新增后台重试系统。

补充 dev 实证：config_flow 仅在同步函数抛异常或 overall success=false 时回滚用户行；sync_service:631-651 在已探测到安装设备且全部投递失败时才返回 false，部分成功或没有可投递设备均可能返回 true。因此不能概括成“任意 Bot 下推失败就回滚”，也没有跨设备回滚补偿。当前 user fanout 给每个 Bot 传同一组用户参数，且底层解析器没有 bot_id；本次必须改为逐 Bot 解析才能保护覆盖值。当前枚举上限 page_size=100，完整覆盖受影响 Bot 的方式需内部实现时核对。

本轮 grill 收口。后续内部设计需核实：单 MCP 事务的仓库边界、受影响 Bot 完整枚举、所有投影入口统一解析、失败时 allow-list 不误删旧项，以及 OCB 各 Engine/provider 兼容。版本和报告结构优先依据 dev 已有合同提出最小扩展；若核查发现必须改变已确认的产品行为，再返回协议评审。

后续范围决定（已确认）：一期暂不处理用户配置/Bot 覆盖并发修改的校验提交竞态，以及旧投影晚到覆盖新配置，接受现有并发风险。不新增跨入口锁、revision/CAS、条件回滚或投影串行/代次机制，不作为交付前置。Q11 常规预检与 Q12 单 MCP 事务仍保留，不承诺跨请求/跨设备强一致。

### Q5 / Q6 已确认

1. 自定义 URL：先按已确认规则从 Center 解析基础端点，再仅用 Manifest URL 替换地址，保持该端点协议；endpoint_env 仍用于选择基础端点，但不证明自定义 URL 的部署环境。允许 URL 不在 Center 已登记地址列表内，MCP 身份仍必须登记。Center 元数据校验不等价于自定义 URL 连通/握手验证，投影仍 best-effort。只传 URL、不传 endpoint_env/transport_protocol 时，最终地址直接使用该 URL；环境/协议仍沿用 user config 和既有默认选址规则，不从 URL 字符串猜测协议，不跳过 Center 身份及权限检查。

安全收口：后端生成的 server 静态配置中，自定义 URL 不继承 user `api_key`、
user/default Header 或平台托管 Secret；仅保留同一 Bot Manifest 条目显式声明的
非敏感 Header。容器级 mcporter `headerPolicies` 仍会按 host 动态注入 Header，
它不属于单个 server config，当前链路不能按 Bot 条目关闭。因此自定义 URL 若命中
既有 policy host，仍可能收到动态 Header；这是发布前必须明确接受或另行限制的安全边界。
2. 重复提交与移除：采用下文完整 Bot 覆盖声明语义。mcp 省略不处理；声明条目时省略 config 撤销该条目旧 Bot 覆盖；headers 省略恢复用户来源、{} 明确空集合；移除 MCP 同时清理 Bot 覆盖，不影响用户配置。null 与 URL 空字符串拒绝，避免多套清空语法。

只传 URL 的示例（MCP 区域片段）：

```yaml
mcp:
  - server_code: mcp.example
    config:
      url: https://custom.example.com/mcp
```

此例最终 URL 为声明值；协议从已有配置/Center 解析，但不会携带继承凭据或
Header。环境未配置时既有默认是 PROD；协议无偏好时既有逻辑优先
STREAMABLE_HTTP。

### 配置字段现状核查与建议（未定案）

- models/mcp.py:112 起：api_key 列标记为 LING_XI 向后兼容；另有 custom_headers、extra_config、env、租户及用户/server 身份字段。
- mcp/services/config_service.py:69：统一读取优先 extra_config.api_key，空值 fallback 独立 api_key 列；headers 读取 extra_config.headers。统一写入同步 api_key 列与 extra_config.api_key，不更新 custom_headers 列。同步 payload 对 api_key 只读取 extra_config，旧列 fallback 与统一读取存在差异，不能将存储重复字段当成两套凭据。
- devices/services/mcp_device_payload.py:71 起：api_key 为 authorization=xxx 时拼 URL query，为 x-ling-auth=xxx 时生成同名 Header；该设备路径并不通用支持任意 key 或裸 token。
- endpoint_env 是 Center 端点的 PROD/PRE 选择器；数据库 env 是系统配置数据隔离环境，两者不同。
- transport_protocol 是 Center 端点协议偏好 SSE/STREAMABLE_HTTP；指定协议不存在时当前设备路径回退首个可用端点。无显式偏好则优先 STREAMABLE_HTTP。最终映射 mcporter transport=http/sse。
- headers 是自定义 HTTP Header 集合；平台默认 Header、平台托管 Header、由 api_key 生成的鉴权以及运行时 headerPolicies 另有组合过程。headers={} 只表达空的 Bot 自定义 Header 集合，不代表禁用全部鉴权。
- 最新范围：一期配置包含 url、headers、endpoint_env、transport_protocol；不暴露 extra_config 存储包裹。api_key 属凭据字段，与已确认一期只传非敏感配置的范围不符，建议不新增入口；既有用户 api_key 兼容保留。
- 选址：Manifest 显式提供环境/协议时优先，未提供时沿用 user config 及既有默认选择。后端读取 Center 元数据校验选择。自定义 URL 按 Q5 仅替换最终地址；Center 元数据不能单独证明自定义 URL 支持该协议。
- 用户提到的 /api/mcp/market/detail 按 server_code 返回 MCP 服务详情；用户的已保存配置来自单独的配置读取流程。二者分别是可选端点元数据和用户选择。此轮核对了本地 router 源码，未调用含会话参数的线上 URL。

### 新增/修改/删除的解释（已确认）

现有 MCP 集合是目标状态：省略 mcp 保持原状；非空列表新增缺少的安装、保留仍声明的安装、移除未声明的非平台默认安装；[] 清空管理集合。移除 Bot 安装不删除 Center 注册或 user config，也不承诺物理删除容器残留 entry。

配置语义：当 mcp 被声明时，每个条目的 config 为完整 Bot 覆盖声明；省略 config/config={} 恢复全部用户/中心来源；省略某个配置字段撤销该字段旧 Bot 覆盖；headers={} 明确不继承用户 headers，headers 中删除某个 key 后它不再属于 Bot 自定义 Header 集合。URL 空字符串与 null 拒绝，恢复 Center 选址通过省略 url 表达。移除 installation 时一并撤销 Bot 配置，避免重新安装继承陈旧覆盖。

资源范围 → 字段集合与 URL/transport 约束。

继承策略 → 缺省、清空、移除、模式切换和用户默认值变化的具体行为。

凭据引用分支：一期延期；后续另行评审引用 schema、授权主体、跨环境映射、轮换及错误合同。

上述分支完成后 → dry-run/plan 与 apply 返回、best-effort 投影边界、兼容与验收。
