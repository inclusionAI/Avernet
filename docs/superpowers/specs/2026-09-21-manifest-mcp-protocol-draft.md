# Manifest MCP Bot 配置协议：一期评审稿

状态：核心业务语义已确认，并作为本次实现的合同基线。Manifest schema v1 以可选
`mcp[].config` 做向后兼容扩展；仅含 `server_code` 的既有文档保持原语义。

## 1. 目标与范围

在已有 Manifest 的 manifest.mcp 中，声明 Bot 要安装的 MCP 及其专属连接配置。MCP 必须已在 MCP Center 登记，沿用现有权限校验；配置只影响目标 Bot，不修改 user config 或其他 Bot。

一期 Header 为非敏感明文字面值。凭据引用与统一数据安全方案后续另行设计。本文不增加 api_key、secretRef、任意 stdio command/args 或 headerPolicies 配置入口。

## 2. YAML 形状

下例沿用现有 schema_version: 1 文档外层，展示候选扩展；最终版本发布策略尚待确认。server_code 为示意，实际填写 Center 已登记的值。

```yaml
schema_version: 1
manifest:
  mcp:
    - server_code: mcp.example
      config:
        endpoint_env: PRE
        transport_protocol: STREAMABLE_HTTP
        url: https://custom.example.com/mcp
        headers:
          X-Project: project-a
```

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| server_code | string | 是 | Center 中已登记的 MCP 标识 |
| config | object | 否 | 该 Bot 对该 MCP 的完整覆盖声明 |
| config.url | string | 否 | 自定义远程 MCP endpoint，替换最终连接地址 |
| config.headers | map<string,string> | 否 | 完整 Bot 自定义 Header 集合，整组替代用户 Header |
| config.endpoint_env | string | 否 | PROD 或 PRE；选择 Center 基础端点的环境 |
| config.transport_protocol | string | 否 | SSE 或 STREAMABLE_HTTP；选择 Center 基础端点的协议 |

不暴露数据库 extra_config 包裹或重复的 api_key/custom_headers 存储列。未知字段拒绝；不接受 null 作为清空语法，url 空字符串拒绝。

URL 接受绝对 http/https 地址；Header 必须为字符串键值，拒绝非法换行和大小写重复键。同名平台托管 Header 不允许由 Manifest 覆盖，按大小写不敏感比较并返回字段冲突错误。LOCAL/stdio MCP 仍可仅凭 server_code 安装，但拒绝上述远程配置字段。

## 3. 解析与校验

1. 根据 server_code 查询 Center 元数据，校验注册及访问权限。
2. 环境/协议：Manifest 显式字段优先；未填写时沿用 user config 和既有默认选址规则。没有用户环境配置时默认为 PROD；无协议偏好时现有逻辑优先 STREAMABLE_HTTP。
3. Manifest 显式指定环境/协议时，必须在最终有效组合下匹配同一个可用 Center 端点。不匹配则报校验错误，不静默回退其他协议。仅继承旧用户偏好时的兼容行为不因本扩展自动重写。
4. 传入 url 时，以其替换所选基础端点的地址，协议保持解析结果。url 不必等于 Center 登记地址；endpoint_env 不保证自定义 URL 的真实部署环境。
5. 后端生成的 server 静态配置中，自定义 URL 不继承 user `api_key`、
   user/default Header 或平台托管 Secret。需要的非敏感 Header 必须与 URL 在
   同一 Bot `config.headers` 中显式声明。容器级 mcporter `headerPolicies` 仍会
   按 host 动态注入 Header，当前协议没有按 server 关闭它的字段；若 URL 命中
   已配置 policy host，仍可能收到动态 Header。
6. 未声明 headers 时继承用户 Header；声明时整组替代，不逐键合并。平台托管 Header、既有凭据转换和运行时动态鉴权仍按平台规则处理，因此 headers={} 不等于 HTTP 请求没有任何 Header。

### Q5：环境/协议与 URL 的关系

所有场景都要校验 MCP 注册及访问权限。环境/协议逐字段按 **Manifest > user config > 现有默认规则** 取值。

| Manifest 传参 | 后端如何选端点、做校验 | 最终 URL | 最终协议 |
| --- | --- | --- | --- |
| 环境/协议至少传一个，不传 URL | 补齐未传项后，匹配 Center 同一可用端点；不匹配报错，不静默回退 | Center 所选地址 | 所选端点协议 |
| 环境/协议至少传一个，同时传 URL | 同上；自定义 URL 不跳过 Center 校验 | Manifest URL | 所选端点协议 |
| 只传 URL | 沿用 user config 和既有默认选址规则 | Manifest URL | 所选端点协议 |
| 三者都不传 | 沿用 user config 和既有默认选址规则 | Center 所选地址 | 所选端点协议 |

URL 只覆盖地址，不改变协议。Center 校验不保证自定义 URL 的实际环境、连通性或协议兼容性。
后端静态 server 配置仅携带同一 Manifest 条目显式声明的非敏感 Header；容器级
`headerPolicies` 仍按 host 生效，不受该条目的 `headers` 或 `{}` 控制。

## 4. 安装集合与配置的增删改

| 写法或变化 | 结果 |
| --- | --- |
| 省略整个 manifest.mcp | 完全不处理 MCP 安装和 Bot 配置 |
| mcp 中新增 server_code | 新安装并保存其声明配置 |
| 保留 code，修改 config | 更新该 Bot 配置；配置变化必须触发投影，不能按已安装直接判定 unchanged |
| code 仍在，但省略 config 或 config: {} | 清除旧 Bot 覆盖，恢复用户配置/Center 来源；安装保留 |
| 省略某个 config 字段 | 撤销该字段旧 Bot 覆盖，恢复对应来源 |
| headers: {} | 明确空的 Bot 自定义 Header 集合，不继承用户 Header |
| 从 headers 中删掉一个键 | 新的 Bot 自定义 Header 集合中不再包含它 |
| 从 mcp 列表移除 code | 移除安装并清理对应 Bot 配置；不删除用户配置及 Center 注册 |
| mcp: [] | 清空可管理安装及对应 Bot 配置；平台默认 MCP 除外 |

非空 mcp 列表是完整目标集合，包含 UI 已安装项在内；不要只填写本次新增项。移除是逻辑安装/配置语义，不承诺物理删除容器所有残留连接条目。

沿用现有整份 Manifest DELETE 的“不再声明”语义：不等于 mcp: []，不应把删除 Manifest 文档解释成卸载此前落地资源。

恢复继承示例：

```yaml
schema_version: 1
manifest:
  mcp:
    - server_code: mcp.example
```

明确空 Header 示例：

```yaml
schema_version: 1
manifest:
  mcp:
    - server_code: mcp.example
      config:
        headers: {}
```

## 5. 生效与反馈

保存 Manifest 时做字段/类型等格式校验；apply（含 dry-run）时查询 Center 并校验端点组合及权限。保存成功不表示 Center 校验已经通过。执行写入前先检查整个 MCP 列表；任一此类校验失败，本次 MCP 类目不开始写入，其他类目沿用既有独立处理规则。

沿用 Skills/MCP 的 best-effort 运行时投影语义。文档保存、配置 apply 持久化与容器连接成功是不同状态；不能把接受配置直接表述成工具调用成功。离线、重启后投影必须使用已持久化的 Bot 配置。

同一个 Bot/MCP 的安装关系和覆盖配置在同一数据库事务内新增、修改或删除，提交成功后才下推。单条失败不能留下“安装成功但配置未保存”等半套状态；不同 MCP 之间仍可能部分完成，不承诺整个 Manifest 原子提交。运行时投影失败不撤销本次已成功提交的 Bot 配置。

如果 Center 后续移除对应端点，或元数据查询失败，导致重启/重投影无法解析：保留数据库配置并报告该 MCP 本次未同步，不自动改协议、清除覆盖或删除容器旧配置。新容器没有可用配置时，该 MCP 暂不可用；Center 恢复后通过后续既有同步/重投影入口再次处理，不承诺立即自动恢复。这不改变权限撤销的独立处理规则。

协议错误至少需定位到条目/字段，说明未知字段、无权限、端点组合不匹配等原因。下面只表示错误含义，未冻结返回 JSON、错误码或 HTTP 状态：

> manifest.mcp[0].config.transport_protocol：该 MCP 在 PRE 环境没有可用的 STREAMABLE_HTTP 端点。

已有安装但配置不同应在计划/结果中体现更新；具体 updated 状态或等价表达需与现有返回合同对齐。现有 write 阶段可能部分完成，不在本文承诺整个类别数据库事务回滚。

## 6. 评审验收场景

- 同一用户两个 Bot 使用同一 MCP，不同 Manifest Header/URL 互不影响，user config 不被改写。
- 只传 server_code 继续兼容；只传 URL 使用声明地址，保留既有协议解析。
- 显式环境/协议组合匹配成功；分别存在但组合不存在时失败，不回退。
- headers 缺省与 {} 可区分；删除配置恢复继承，不保留旧 Bot 覆盖。
- 仅修改配置也会下推；重复应用相同声明不产生虚假的更新。
- 移除 MCP 清理 Bot 覆盖；再次安装不复用旧覆盖；平台默认项保留。
- 重启/离线恢复仍能使用 Bot 配置；投影失败不被报告为实际调用成功。
- 单个 MCP 的安装关系与配置写入故障时共同回滚；提交后的下推失败保留数据库期望配置。
- Center 端点消失或查询失败时，不静默换协议、不清除 Bot 覆盖；旧容器配置保留，新实例如实报告不可用。
- 用户默认值更新引入新的 Bot 端点组合冲突时，拒绝本次用户更新，旧用户配置不变。

## 7. 待细化事项与内部实现边界

剩余细节：版本发布策略、校验错误结构；平台托管 Header 范围及 URL 目标约束的实现。dev 的 apply 已有 updated 枚举，可复用以表达 MCP 配置更新。Center 不可用时不能假定元数据校验已通过。

内部工作：Bot 维持久化、配置解析、差异计算、安装/移除关联清理、各投影入口、重启恢复及 OCB 企业适配。已确认保留用户配置表、新建 Bot 配置表，只存显式覆盖；存储结构不进入业务 YAML。

用户默认配置更新时，先以候选用户值和各 Bot 显式覆盖重新校验有效端点组合。若本次修改引入新的不兼容组合，则拒绝整个用户更新、保留旧用户配置，并返回冲突 Bot 和原因；不自动修改 Bot 覆盖或回退协议。Center 查询失败单独报告为无法完成校验，不伪装为配置冲突。

一期并发边界：上述预检基于本次读取的配置，不新增用户更新与 Bot 更新之间的并发原子性保证；设备下发也不新增版本或顺序保证。用户已确认本期接受这两项限制，单 MCP 数据库事务与 best-effort 投影规则保持不变。

依据：2026-09-21-manifest-mcp-contract-review.md 的已确认决策。当前按用户要求使用 dev：Avernet `2c943c8f6fa929d02337cd3b26d0ec74fb5cfd6d`；OCB `b04a9348e8dcbfe1018ccc9fd0f76e6fb05c633e`，其 public gitlink 为 `ded8c971ef380aba6676916d51f2f2cd3baf9bbf`。两者不是同一个公共提交；相关目录差异和企业适配沿依赖链核对，未做部署或运行验证。此前 release 基线保留在评审记录中。
