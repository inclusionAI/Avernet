# 桌面 Bot 迁移实施记录

## 范围与兼容边界

按用户确认迁移旧 Web 的 OpenClaw/Hermes 默认桌面能力。MemoryOS、Wiki、动态模板未使用，不纳入本期。头像上传/生成及外部链接解析允许既有 SDK 通道。生产代码不注入 Mock；测试 Mock 不能证明真机兼容。

后端和新前端均在用户指定的当前分支修改；旧前端与桌面 App 没有修改。未提交、推送或部署。旧 Router 保留，新接口通过 Service/Protocol 复用业务逻辑。

## 功能落地与验收边界

下表的“接入/保留”指代码路径，均不表示已通过真实设备验收。

| 基线项 | 本次落地 | 验证范围 |
| --- | --- | --- |
| F01–F05 创建与授权 | 独立本地创建入口；设备分页/刷新/自动选择；目录响应校验及自动 mount_path；只开放两引擎；本地授权 GET 轮询和原始请求保留 | Service/组件 Mock；Chrome 创建表单实际提交请求核对 |
| F06–F08 状态 | 新增按 Bot 寻址的启动进度；四步与真实百分比/失败展示；重新查询；离线/无设备 teamclaw://open | 进度纯逻辑与后端 Mock；客户端唤起仍待设备 |
| F09–F11 生命周期 | 重启/重启引擎均按旧桌面语义调用 local restart；目录打开；删除后清理当前会话选择和身份会话记忆 | 动作/Hook 回归；WS 页面卸载使用既有 disconnect 清理 |
| F12 元数据 | 复用名称/描述更新；新增头像 GET/PUT；列表与聊天头像；公开版 DiceBear、内部 SDK 上传/生成懒加载 | HTTP owner/写入合同；SDK 实际上传/生成待外部环境 |
| F13–F14 会话 | desktop draft runtime gate；既有会话/模型/历史/收藏/搜索/停止流程复用；direct 原样 localhost URL，不请求 IAM；重连重新发现动态端口，取消后不复活 | runtime/连接/前端 Provider Mock 回归；真实流式和旧历史待设备 |
| F15–F17 配置和文件 | OC MD/引擎配置保留；两引擎资源只读、1 MiB 预览上限；根/子目录打开；隐藏会话文件上传 | 能力策略/资源回归；实际文件读写与配置生效待设备 |
| F18 关联链接 | 新增 OpenAPI list/batch create/update/delete；原有语雀解析/权限同步；UI 批量添加、源链接预览、类型/名称/URL/权限更新；失败后重读服务端 | HTTP/Workflow 权限、重复与持久化错误测试；外部解析/同步待真实服务 |
| F19–F20 Skill/MCP | 复用既有 SkillSet/MCP/市场 OpenAPI；补本地 Skill ZIP 上传/替换、启停、删除和参数表单（frontmatter schema） | 参数解析和既有编辑器回归；实际激活/挂载待 Runtime |
| F21–F22 副屏/任务 | 保留 OC 既有配置/会话渲染及定时任务入口；Hermes 不开放对应入口，避免请求不支持能力 | 编辑器/能力策略；实际渲染和调度待设备 |
| F23 协作 | 复用现有 BCN/群聊/好友链路；桌面禁止无需确认；Hermes 不显示为 BCN 身份 | 身份/审批回归；真实协作待 BCS/App |
| F24–F26 范围限制 | 禁用桌面健康检查、渠道/节点/审批等不支持入口，保留发布限制；不扩展管理台；动态入口按用户确认排除 | 策略/静态检查 |

旧版机器 ID 展示位于创建设备列表，挂载目录输入 JSX 已注释；新版保持设备选择和自动路径，不额外设计手选目录。启动失败“重试”旧版仅重新轮询，新版使用“重新查询”保持该行为。

## 后端 OpenAPI 改造

新增 7 个方法（前缀 `/openapi/v1/bots`）：

- `GET /{bot_id}/local/start-progress`：owner/grant 校验后解析 Bot 的 device_id，调用现有 BaaS 进度服务；原样保留实际进度。
- `GET/PUT /{bot_id}/avatar`：复用 BotService；PUT 请求 `{ "avatar_url": "..." }`，空串可清除。
- `GET/POST /{bot_id}/links`：POST 请求 `{ "links": [{ "url": "...", "name": "...", "link_type": "yuque|dima|antcode", "access_modes": ["READ"] }] }`。
- `PUT/DELETE /{bot_id}/links/{resource_id}`：PUT 可省略不修改字段，拒绝显式 null；先检查资源归属。

更新 `GET /{bot_id}/connection` 的桌面分支，增加 `transport_mode`；云端/friend 响应保持原有形状。LocalBot 增加明确的 `bot_type=desktop`。新增 API 纳入 admission、authorization 及网关 schema；权限表按职责拆分，源文件不超过 1000 行。

现有 devices/files、local create/auth-status/restart/delete/open-folder，以及 identity/config/resources/skills/skill-sets/MCP/routines/render-screens/collaboration 继续复用原有 OpenAPI。运行时 gate 增加 desktop draft 支持，不开放 desktop verify/online/config-manifest。

### 配置

Backend 用户配置省略此项默认 direct；如需后续启用 relay，由部署配置设置：

```yaml
desktop_connection:
  mode: relay
```

仅允许 direct/relay；未知键或非法值启动时拒绝。direct 仅接受 BaaS 返回的 loopback `ws` 地址、显式端口和正确引擎路径。relay 使用既有代理链路，本期没有产品开关；真实 relay 仍需部署验收。

### 错误与回退

链接批处理先预校验重复和解析，但写入与外部授权同步不是跨系统原子事务。保存后同步失败会返回错误，可能已有部分结果落库；前端失败后刷新实际状态，不能宣称整批回滚。旧共享权限同步仍默认 best-effort，新工作流显式 strict，旧消费者行为不变。

先增量部署 Backend，再灰度新前端。回退入口可返回旧 Web；不复制 Bot/会话/目录，不设计不可逆数据迁移。回退不撤销已经写入共享数据的用户编辑。

## 验证记录

最终已执行结果：

- Backend engine_runtime、bot_inventory、architecture、全部 OpenAPI v1：2334 passed，2 skipped。
- 旧桌面/资源 Service、旧 desktop/resources API、desktop resolver 兼容回归：432 passed。
- 网关 served OpenAPI / route security：94 passed。
- 测试文件按 1000 行限制拆分后定向连接验证：2 passed；减少架构测试无关格式改动后：8 passed。
- Frontend 迁移相关 Service/组件/Hook/聊天/身份/协作回归：67 suites、427 tests passed。
- TypeScript、lint、工程 guardrails、内部生产构建通过。构建仍有 bundle size 建议告警，未视为失败。
- Python Ruff check、所有改动源文件长度检查、新旧仓库 diff 范围和 whitespace 检查通过。旧前端工作树仍干净，无 App 改动。
- 最新 Open Core 导出及边界静态检查通过。

Mock Chrome 本地创建提交了正确 machine_id、engine 和自动 mount_path，弹窗成功关闭，无页面脚本异常。浏览器其它模块未完整 Mock，仍出现后端加载错误，本次浏览器只覆盖创建表单。

公开版全量 `ci:open-core` 已尝试：导出静态检查通过，但 `npm ci` 从 registry.npmjs.org 下载发生 EIDLETIMEOUT，后续公开版 typecheck/test/build 未执行。内部版构建另行执行，不以内部成功替代公开版成功。

## 仍需真实环境的验收

用户暂无 App/BaaS/OC/Hermes 测试环境，因此以下尚未完成：两引擎全生命周期、实际聊天/停止/重连、旧历史原地可见、文件读写/配置生效、Skill 实际激活、任务执行、副屏、BCN、外部头像与语雀授权，以及旧建新用/新建旧用交叉回归。Mock 与单测不替代这些结果；目前不宣称已经完成生产或全功能真机验收。
