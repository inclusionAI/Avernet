# Agent 监控搜索与分页实施记录

更新：2026-09-20
分支：`feat/clawinsight-monitoring-bot-search`
依据：[v2.7 Spec](./clawinsight-monitoring-bot-search.md)

## 1. 当前结论与边界

**Avernet 监控功能和 OCB 最小接线已实现；真实页面已在本地 HTTP 服务上运行并完成下述交互检查。当前是本地开发/联调版本，不代表已通过生产 SSO、MySQL/OceanBase 或采集端联调。**

- Avernet 分支 `feat/clawinsight-monitoring-bot-search`；OCB 在原有 `fix_monitoring_without_bot_config` 工作分支上做必要接线，保留原有未提交修改。
- 根据用户最新授权，修改 Avernet 公共实现及 OCB 启动/认证接线。没有修改 claw-validation，没有生产 DDL、迁移、回填、提交、推送或部署。
- 使用新单数表 `insight_monitoring_bot_check` / `insight_monitoring_diagnose`，不回退或双写旧表。
- 已从代码定位到登录服务、专用管理员名单和 ac_bots 连接，不再需要用户提供抽象的 Provider 契约。未装配的其他宿主仍返回 NOT_READY，不猜测身份。
- 保留建分支前已有修改，尤其是“持久化检查状态不按心跳时间过期”；OCB 既有 access-admin/模块角色改动不属于本轮新增实现。

## 2. 已实现内容

### 2.1 存储与 v1 兼容

| 位置（相对 `clawinsight`） | 实现 |
|---|---|
| `server/services/monitoring/contracts.ts`、`target.ts` | 内部统一 `(botId, entityId, env)`，验证并保留大小写/前导零 |
| `server/repositories/monitoring-schema-check.ts` | 新表只读检查：列、类型、可空性、默认值、二进制比较、索引及 SELECT 权限；拒绝 checks 中更严格的子集 UNIQUE |
| `server/repositories/monitoring-repository.ts` | 查询、插入、CAS 更新、重复事件比较全部使用三字段；相同 target 不允许 engine 切换 |
| `server/repositories/monitoring-summaries.ts` | 每批最多 50 个目标，单条 SQL 聚合 checks 与诊断摘要；无 checks 一律未监控 |
| `server/services/monitoring/target-resolver.ts` | 在可信 tenant/env 范围精确解析旧报送 ID；无匹配/歧义拒绝写入；可选经审核的一对一只读别名绑定 |
| `server/services/monitoring/monitoring-service.ts` | 保持 v1 DTO、eventId、幂等键及 ACK；旧 discovery/详情使用同一解析器，回显已核实的旧别名 |
| `server/services/monitoring/schema.ts` | 仅供显式本地 SQLite 初始化；生产不调用自动建表，不回退旧表，不双写 |

诊断事件仍按 `event_id` 唯一。不同 target 的 event_id 碰撞返回 409；接收端不重新计算 eventId。诊断查询保持计数与列表同一 SQL、稳定排序及 LIMIT/OFFSET，不因跳页功能增加表结构或索引变更。

### 2.2 目录与后端权限

- `SqlMonitoringBotDirectory` 只读使用**显式提供的** `ac_bots` 连接，读取 `bot_name`、`bot_id`、`entity_id`、`env`、`owner_id`、`owner_name`。不会自动使用监控库替代目录库。
- 成员按 `owner_id === verified staffId` 限权，不以 entity_id/协作者代替 owner。管理员可选择 mine / monitored / all，仍受 tenant、env 和软删除约束。
- 成员搜索名称/ID；管理员额外搜索 owner 工号。参数化并转义 LIKE 通配符。
- 目录按 id DESC 游标分页。SQL 将 BIGINT id 转为字符串返回，避免 JavaScript 超过 `2^53` 后出现分页精度丢失。
- 跨连接 monitored 搜索单次最多扫描 200 个目录候选，每批最多 50 个；未消费位置保留在 cursor，允许短页/空页继续加载，不把预算耗尽伪装成末页。
- botRef 是版本化 base64url 目标引用，**未经签名，不是授权凭据，也不是加密的秘密**。不新增签名密钥或 token 表。每次详情/加入请求重新读目录并校验 owner、tenant、env。
- cursor 有效期 15 分钟，绑定工号、角色、tenant、env、scope、查询、limit 与时间窗口。
- 新增 options / target status / target diagnoses / enrollments 路由；旧读路由保留原管理员限制，报送网络边界未放宽，治理权限未改。
- 加入占位无任何写操作：未接入返回精确的 501 业务码，已接入返回 409，真实 401/403/404/503 不伪装成“功能待开放”。

### 2.3 页面与交互

- 已登录成员可进入监控模块；账号/角色变化重建面板，后端权限仍为最终裁决。
- 关闭选择器不平铺 Bot；默认从 mine 首批选择自己的目标，mine 为空时不会在管理员切换到全部范围后自动选择别人的目标。
- 普通用户与管理员采用不同搜索提示，250ms 防抖，中文 IME 完成后才请求；旧请求取消/失效，搜索草稿变动立即隐藏旧候选。
- 候选统一默认头像与实线卡片；名称、ID、工号及右侧“监控中 / 已暂停 / 未监控”，无多余归属描述和加入按钮。
- 同批出现同 owner/ID 跨环境目标时显示小型环境标识，其他情形通过 ID tooltip 提供 env。摘要独立展示已诊断会话/诊断/告警；会话缺标识时显示缺失说明，不用诊断量冒充会话量。
- 候选区保持约 2–3 行高度、有限滚动；在底部点击“加载更多 Bot”按需读取，不在空页时自动无限请求。打开/手动刷新重新取目录，收起时不轮询目录。
- 搜索框向下键进入候选，候选上下/Home/End/Enter、Escape 返回触发器、外部点击收起。弹层根据可视视口调整位置/宽高，监听滚动、缩放和尺寸变化。
- 未监控主面板提供“加入监控”，仅精确 501 显示已确认的用户提示。关闭恢复焦点；卸载后的迟到请求不刷新其他 Bot。
- 当前目标可见时每 30 秒轮询；未接入不请求诊断。401/403/404 清除选择、摘要和记录；监控清单在 status 与 diagnoses 之间变化时清除旧历史，回到未监控主面板。
- 分页包含首末页、上一/下一页、最多三个相邻数字页码、直接跳页、10/20/50 条；无效草稿不请求，空数据为 `0 / 0 页`。切目标/筛选/条数回到首页，轮询保留当前页、展开项及跳页焦点。
- 页面缩减最多自动纠正一次末页，失败保留明确重试路径，不把旧记录配给新页码。
- TC 文本宽度保持桌面 160px/中屏 140px，track 增加 16px 并增加 16px 右侧留白；长标签单行省略，通过 title/详情读取全文。表头和记录同列对齐，窄屏沿用堆叠排版。
- CSS 按布局、记录、选择器、分页拆分，构建统一复制；没有手工修改 dist，也没有复制被否决的整页分页 Demo。

### 2.4 时间窗口的明确协议

新查询接口 `start` / `end` 使用 UTC epoch 毫秒和 `[start,end)`：

- 两个参数均省略：当天北京时间自然日。
- 显式指定窗口时两个参数都必须提供；有限边界递增且跨度不超过 366 天。
- `start=all&end=all`：沿用页面“全部时间”，包括 occurred_at_ms 为 NULL 的历史记录。
- 一端 `all`：该端不设界限；另一端为有效毫秒数。存在时间条件时不包含 NULL 时间。
- 不用空字符串或省略单边参数隐式表达全部时间。候选和当前目标查询共用窗口，候选摘要不受详情分页/结论/关键词影响。

## 3. 复用已有 OCB 实现的最小接线

### 3.1 复用点与修改位置

| 既有能力 | 本轮处理 |
|---|---|
| `EvolveRepository(db)` 查询 ac_bots | 在同一既有连接上创建 Avernet `SqlMonitoringBotDirectory`；目录只执行读操作，底层共享连接不是新建只读账号 |
| archive `routes/auth.ts` 的 Buservice / Asfagent 登录查询 | 导出 `resolveVerifiedLoginUser`，在服务端验证 Cookie；不直接把浏览器工号、管理员标记或本地解码 JWT 当作认证结果 |
| `AdminUserRepository.listEnabled()` 的 `clawInsightAdmins` | 每次监控请求重查专用角色；不把治理管理员自动升级成监控管理员 |
| bootstrap `createInsightRouter` 的 options | 传入 `createInternalMonitoringRuntime(db, …)`；公共业务/目录查询不复制到 OCB |

OCB 本轮生产源码落点：

1. 新增 `internal/bootstrap/clawweb/server/monitoring-runtime.ts`。
2. `internal/bootstrap/clawweb/server/index.ts` 增加导入和 monitoring 参数。
3. 在已有修改的 `internal/modules/archive/server/routes/auth.ts` 上追加已验证身份解析、失效清理，补齐远端登录成功分支的角色缓存字段；拒绝 Asfagent 明确 success=false 的响应。

另新增 `monitoring-runtime.test.ts`、`verified-login.test.ts`。原有 access-admin、admin-auth、角色 UI 改动保留，不重新实现权限管理系统。

### 3.2 认证与目录边界

- 已验证身份缓存 30 秒、最多 500 项，以 Cookie 与登录服务地址的 SHA256 为键；不保存原始 Cookie 为键，不缓存管理员判定。登录失效入口同步清除身份缓存。
- 现有 `/auth/me` 策略没有全局重构；监控服务独立验证身份，前端角色提示不能代替后端 ACL。伪造工号/角色字段以及手工编码别人的 botRef 均不能越权。
- 内部租户沿用 teamclaw，目录环境范围 dev/pre/prod/gray。只读检查 ac_bots 列：存在 avernet_tenant 则加租户过滤；成功确认无列才按内部旧 schema 处理。检查失败返回 NOT_READY，后续允许重试，不能因失败撤掉隔离条件。
- `entity_id + bot_id + env` 标识目标；成员归属仍以目录 `owner_id` 匹配已验证工号。目录查询、诊断摘要、详情和加入占位共用同一授权边界。
- botRef 和 cursor 均是不可信编码；cursor 的 SHA256 是上下文摘要而非签名，不声称防伪。查询参数、范围和权限仍在服务端重校验，引用本身不授予权限。
- 旧内部报送协议不变；如果旧 botId 在目录中对应多个 owner/env，明确拒绝歧义。上线前需核对现有采集目标能否唯一解析，不能以“不改采集端”推导所有实例已经兼容。

### 3.3 可运行的本地页面

在 `src/evolverun/clawweb/public/modules/clawinsight` 执行（预览使用 Node 原生 SQLite，要求 Node >=22.13）：

```bash
npm run monitoring:preview
```

- 普通用户：`http://127.0.0.1:3101/preview/login/member`
- 管理员：`http://127.0.0.1:3101/preview/login/admin`
- 也可用页面右下角“本地预览”折叠控件中的角色链接切换；同一浏览器共用本地角色 Cookie，不是两个独立登录会话。

`preview/` 渲染实际 `InsightCenter`，通过 HTTP 调用实际 Express router、权限服务、SQL 目录和监控 repository，不是另一套静态 HTML 或前端拦截 fetch 的 mock。仅身份与 SQLite 数据为合成测试夹具，不连接生产数据库或真实 SSO；服务强制绑定 127.0.0.1，生产模块不导入 preview。

夹具包含 32 个 Bot、两个 owner、同名 default 跨 owner/env、已暂停和未监控目标；主要目标有 163 条诊断、82 个已诊断会话、55 条告警，可验收超过 5 页的跳页。旧 local-host 的原生 better-sqlite3 启动限制仍存在，新的 preview 不依赖该路径。

## 4. 本地验证（2026-09-20）

| 验证 | 结果 |
|---|---|
| `npm run check` / `npm run build` | 通过 |
| `npm run check:monitoring-preview` | 通过，包含真实页面依赖与预览服务 |
| 监控 UI / 导航 / API / 弹层几何专项 | 81 / 81 通过 |
| `MONITORING_TEST_SQLITE_DRIVER=node npm run test:monitoring` | 131 / 131 通过 |
| `npm run test:monitoring-preview` | 4 / 4 通过，真实 HTTP + SQLite SQL |
| OCB 新增接线/登录验证及既有 auth 回归 | 17 / 17 通过；直接加载当前源码，使用临时 workspace alias 配置，不是全量 OCB 构建 |
| OCB 本轮接线/auth 源码定向 TypeScript 检查 | 通过；非完整 bootstrap 类型检查 |
| `git diff --check`（两仓库） | 通过；Avernet 49 个修改/新增源文件均 ≤1000 行，最大 711 行；OCB 原有 bootstrap 1111 行增加两行接线，未做无关拆分 |
| 真实浏览器基础交互 | 已执行，详见下文 |
| 生产 SSO、实表、真实采集端、MySQL/OceanBase | 未执行 |

HTTP/权限测试覆盖：成员只能见自己的目录及 20+10 候选分页、名称/ID 搜索、管理员工号搜索、跨 owner/env 的 default 隔离、诊断163/告警55/会话82的独立统计、末页、加入501且无数据库写入；OCB 覆盖伪造请求字段、登录失败、角色变化、目录 schema 失败关闭及重试。手工构造格式合法但属于别人的未签名 botRef，成员的状态/诊断/加入请求均被拒绝，管理员可正常读取。

真实浏览器已核验：

- 普通用户搜索未监控 Bot 只返回自己同 ID 的目标；统一候选头像、三状态、有限高度列表及统计展示。
- 未监控主面板点击加入，显示“加入监控功能正在开发中，敬请期待。”，关闭后保持原目标。
- 管理员有“我的 / 诊断范围 / 全部”范围；按其他工号搜索得到两个目标，诊断范围仅保留已监控目标，选择后展示该用户的 9 条诊断/3 条告警。
- 163 条数据的末页为第 9 页（161–163 条）；直接跳第 6 页显示 101–120 条；一键首页返回 1–20 条；切换每页 50 条后显示 1–50 条、共 4 页。
- 早期验收检查过长 TC 标签截断并与会话时间保持留白（已被下述 v2.3 完整换行规则替代）；390×844 窄屏候选弹层截图未超出视口。尚未完成 Spec 所有视口、200% 缩放和键盘组合验收。

前一阶段全模块测试记录为 617 通过、29 失败、105 跳过；不是本轮全量重跑结果。失败集中在其他套件缺失 better-sqlite3 原生绑定及后续 close 错误。没有安装/重建依赖、修改锁文件或替换生产驱动。Node SQLite 验证不等于 MySQL/OceanBase、ZDAS 或生产驱动认证。UI 回归仍有 React act 警告。

## 5. 上线前仍需完成的验证

1. OCB 同步本轮 Avernet 版本后，以真实宿主启动配置做完整构建/启动及登录联调；本地预览验证的是实际监控模块，不是完整 OCB 进程。
2. 只读核对用户已建新表的名称、字段、比较规则和索引，以及 ac_bots schema、内部 tenant/env 与实际存储隔离；不自动修库。
3. 预检当前 claw-validation 配置 ID/实例别名的唯一 owner/env 解析，验证 discovery、重试、ACK 和 eventId 连续性；歧义目标不静默切换。
4. 用生产对应数据库方言/驱动执行受控联调，完成其余视口与缩放验收。
5. 核对内部报送流量边界、维护窗口、新表历史可见性及回滚数据缺口。本轮无发布/迁移脚本、无生产写入。

上述是真实环境的上线验证，不再是要求用户额外设计认证/目录接口的开发前置条件。

## 2026-09-21：宿主导航与 TC 完整展示修正

- 正式 OCB `internal/bootstrap/clawweb/src/App.tsx` 和该目录前端代码无本次差异；效果中心原侧栏样式规则保持不变。
- preview 复制现有 OCB 顶栏/UserMenu 的展示结构，并加载相同 Tailwind 基础重置和字体；原先临时角色顶栏移除，角色切换收进独立预览控件。只是本地宿主外观快照，不是完整 OCB 上线验收。
- TC 布局统一在 `monitoring-records.css`，从 `monitoring-pagination.css` 移除旧覆盖；禁止单行省略，长无空格标签完整换行，TC 列额外右侧留白 32px。
- 本地真实 React/API 合成数据浏览器验收：1280px 下标签文本列与时间相隔 48px，中屏 894px 下相隔 40px，表头/内容对齐；390px 下标签/时间分行、垂直间距 9px，标签 scrollWidth 等于 clientWidth，无页面横向溢出。完整长标识无需展开就可见。
- 未修改生产数据库、未部署，未修改 claw-validation；正式导航的结论来自仓库 diff，不冒充线上截图核验。
- 本轮回归：`npm run check`、`npm run check:monitoring-preview`、`npm run build` 通过；监控 UI/API/导航/CSS 测试 84/84 通过，本地 HTTP 测试 4/4 通过（回环端口测试经授权在沙箱外执行）。现有 GovernanceCenter act/source-map 警告不影响结果。
- 静态核对：原宿主 shell/sidebar 的 36 条规则拆分后逐条一致；本次新增/修改源码最大 720 行；Avernet/OCB `git diff --check` 通过。

## 2026-09-21：TC 间距微调（v2.4，以本节为准）

- 用户允许截断后，将额外右侧留白由 32px 收紧到 12px，保留列宽；长标签恢复单行省略，不再撑高记录。
- 保留 title 与详情全文，导航、侧栏、分页及其他元素不变。浏览器默认 1280px 实测两列间隔 28px，标签单行高度 19px，ellipsis 生效。
- 更新 CSS 契约测试与渲染测试描述，避免继续强制完整换行。上节 v2.3 的几何数据仅记录当时结果，不作为当前验收标准。

## 进一步收紧 TC 列（v2.5，以本节为准）

- 将 TC 弹性列改为桌面 160px、中屏 140px，并删除额外 12px margin；时间列宽度和位置不变，摘要列接收剩余宽度。
- 默认 1280px 浏览器实测：TC 列宽 160px、边界间距 16px，表头偏移 0px，ellipsis 生效。导航、侧栏、分页未改。

## 紧凑三页分页（v2.6，以本节为准）

- 数字区最多三个按钮：开头 `1 2 3`，中间 `当前−1 当前 当前+1`，结尾最后三页；少于三页按实际数量展示。
- 删除重复的首末页数字和省略号/±5 快跳；独立首页/末页、上一/下一页、每页条数、当前/总页数、直接跳页及输入校验均保留。
- 仅修改分页组件与相关测试，不改顶栏、侧栏、TC 列或其他样式。浏览器验收默认桌面 9 页数据，页码区从七项减至三项，footer 同行显示。

## TC 间距适中微调（v2.7，以本节为准）

- TC track 改为桌面 176px/中屏 156px，右侧 margin 16px，保留文本可用宽度 160px/140px；桌面间隔最终为 32px；本地复核确认前次 24px 样式已加载，但视觉变化较小，本次再增加 8px。
- 标签省略、三页分页、顶栏和侧栏均保持不变。
