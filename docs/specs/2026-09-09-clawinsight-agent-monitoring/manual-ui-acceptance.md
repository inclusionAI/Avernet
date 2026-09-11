# Agent 监控自愈：本地人工验收清单

本清单验证：Mock JSON → 真实 POST → dev 业务库 → 真实 GET → Avernet 正式页面。
不是静态 HTML Demo，也不是 AIStudio／完整内部 Host／预发环境验收。

## 1. 启动和保留方式

由开发者确认 meshboot 仍指向 dev 后，在 Avernet 根目录启动：

```bash
CLAWWEB_DEV_CONFIG_FILE="/你的私有开发配置/application-default.yaml" \
MONITORING_UI_MINUTES=240 MONITORING_UI_KEEP_CHECKS=true \
node docs/specs/2026-09-09-clawinsight-agent-monitoring/verify-odc-dev-ui.cjs
```

另一个终端在 `src/evolverun/clawweb/public/app/clawweb` 启动：

```bash
node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort
```

入口：`http://127.0.0.1:5173/insight?module=monitoring`。
看到 `API_ACCEPTANCE_OK` 和 `UI_READY` 后再开始测试。记录日志里的 RUN_ID、MOCK_BOTS 和到期时间。

- 默认保留30分钟；上述命令保留240分钟，参数可设1–480分钟。
- 本轮人工验收期间不要停止API进程。到期或正常 Ctrl-C 会清理本批60条诊断和3条检查快照；不清理其他 Bot。
- 关闭浏览器页面不会清理数据。电脑休眠／VPN断开可能让接口或清理失败；恢复后按本批精确 Bot ID 核对，不能假设一定清理成功。
- `MONITORING_UI_KEEP_CHECKS=true` 每分钟通过真实 POST 更新本批 TE/OC 两个 Bot 的模拟检查状态，使人工验收期间保持正常／异常；不更新 stale Bot，不生成新诊断，不代表连接了 AIStudio。
- 检查快照按 Bot 更新，因此行数保持3，不是每分钟新增两行。
- 不向 prod 写入、不执行建表／迁移，不把私有配置或 Token 交给浏览器。

## 2. 测试数据

Bot ID 都以 `mock-ui-<本轮RUN_ID>-` 开头，通过结尾区分：

| 结尾 | 页面预期状态 | 诊断数 | 用途 |
|---|---|---:|---|
| `-te` | 监控正常 | 30 | Trace ID / Session ID、详情、筛选、分页 |
| `-oc` | 检查异常 | 30 | Session Key、切 Bot 数据隔离 |
| `-stale` | 状态未知 | 0 | 有历史检查但超过300秒未更新 |
| `-paused` | 已暂停 | 0 | 固定配置暂停，不依赖检查记录 |

TE/OC各含10条告警、10条通过、10条无法判断；人工干预为是/否各15条。
每个 Bot 有29条已知会话时间、1条时间未知。会话时间跨生成日及前两日。
在本批2026-09-10晚间生成的数据中，9月10日10条、9月9日10条、9月8日9条，另1条未知。
后续重跑应以当轮生成时间为准，特别是零点附近，不硬套以上日期分布。

## 3. 操作路径与预期结果

正式页面采用已确认的 V2 设计（`docs/design/clawinsight-monitoring-redesign-v2.html`）。点击当前 Bot ID 打开固定 Bot 菜单；点击会话时间打开日期弹层，快捷范围立即生效，自定义日期需点击“应用”，取消不更改查询。

除组合筛选案例外，每条用例开始前将时间选为“全部时间”、清空搜索、选择结论“全部”，并按需恢复10条/页。结论计数随时间和关键词变化，不受当前结论筛选影响；状态行仅展示监控健康与最近成功检查时间，不再展示历史总量。

| 编号 | 操作路径 | 预期结果 |
|---|---|---|
| U01 | 打开入口，选择 `-te` | 监控正常；全部30、告警10、通过10、无法判断10；首页1–10条 |
| U02 | 点击第一条 `mock-trace-0`，再点击一次 | 展开／收起；TC.MCP.DATA、86.0%、人工干预“是”、两段诊断文本；无钉钉通知状态和自愈按钮 |
| U03 | 展开 `mock-trace-1` | 通过、人工干预“否”；无置信度用“—”，不能冒充0%；这不是告警是否已处理 |
| U04 | 滚动至底部，点击下一页，再下一页 | 第11–20条、第21–30条；末页下一页禁用；最后一条为时间未知 |
| U05 | 每页条数改20，再改50 | 重置首页；分别显示1–20/30和1–30/30；不是只改页码文字 |
| U06 | 依次点击告警、通过、无法判断 | 各10条，内容与选中结论一致；切回全部恢复30 |
| U07 | TE下搜索 `mock-trace-29` | 唯一1条，时间未知；清空后恢复30 |
| U08 | 输入不存在的关键词 `no-such-record-xyz` | 清晰显示“没有符合当前条件的诊断记录”；Bot依然监控正常；当前关键词对应的结论计数均为0，不代表历史记录被删除 |
| U09 | 打开时间弹层，本批开始/结束都选择2026-09-10，点击应用 | 列表10条；结论计数为告警4、通过3、无法判断3；状态行仍独立显示监控健康状态 |
| U10 | 打开时间弹层，本批日期选择2026-09-08至2026-09-10，点击应用 | 29条，不包含会话时间未知的1条；时间选“全部时间”恢复30 |
| U11 | 时间弹层中输入开始2026-09-10、结束2026-09-09 | 提示“开始日期不能晚于结束日期”；禁止应用，不发起无效日期查询；取消后保留原条件，修正并应用后正常查询 |
| U12 | 无日期条件时选告警，再搜索 `mock-trace-29` | 0条；因为该记录属于无法判断。改选无法判断后1条。证明条件组合生效 |
| U13 | 清空条件，切 `-oc`，再切 `-te` | OC显示检查异常、列表标识为 `agent:main:mock:…`；TE恢复Trace标识。快速来回切换最终内容与选中Bot一致 |
| U14 | 切 `-stale`，再切 `-paused` | 分别状态未知/已暂停，均无诊断；不能都显示“监控正常” |
| U15 | TE第二页展开一条，点刷新 | 保留页码和展开记录，无重复记录；总数仍30 |
| U16 | TE页面保持可见60–90秒 | 最后成功检查时间自动推进（Mock每60秒上报、页面每30秒查询）；诊断总数仍30，不应期待新诊断自动生成 |
| U17 | 调整浏览器窗口宽度 | 内容随宽度重排，无水平溢出；窄屏导航/筛选换行正常；以实际窗口而非整页长截图判断比例 |
| U18 | 时间弹层修改日期后取消；重新打开后按 Escape | 列表条件不变；弹层关闭，焦点回到时间按钮 |
| U19 | 展开记录，点击 Trace ID 或 Session Key 的复制图标 | 出现复制成功反馈；浏览器不允许剪贴板时明确提示手动复制，不假报成功 |

左侧“Agent 治理”及顶部其他功能不属于本测试宿主的API支持范围；本次不要把这些页面的数据加载失败当作监控模块失败。
接口中断、失败补发、AIStudio重启恢复属于后续开发者/预发联调，不要求你手动停服务或改数据库。

## 4. ODC只读核对

在 **dev 的 agentclawdb** 执行，先把以下 `<RUN_ID>` 替换为启动日志里的完整批次号。不执行 UPDATE/DELETE。

```sql
SELECT bot_id, decision, COUNT(*) AS records,
       SUM(human_intervention) AS intervention_yes
FROM insight_monitoring_diagnoses
WHERE bot_id IN ('mock-ui-<RUN_ID>-te', 'mock-ui-<RUN_ID>-oc')
GROUP BY bot_id, decision
ORDER BY bot_id, decision;
```

预期6行：每种结论10条，其中人工干预“是”5条，总计60条。

```sql
SELECT bot_id, status, checked_at_ms, last_successful_check_at_ms
FROM insight_monitoring_bot_checks
WHERE bot_id IN (
  'mock-ui-<RUN_ID>-te', 'mock-ui-<RUN_ID>-oc',
  'mock-ui-<RUN_ID>-stale', 'mock-ui-<RUN_ID>-paused'
)
ORDER BY bot_id;
```

预期3行：TE为HEALTHY、OC为ERROR、stale数据库原始值HEALTHY但时间已过期，页面据此显示“状态未知”；paused来自固定配置，所以没有检查行。这些差异是预期逻辑，不是漏写数据。

## 5. 反馈方式

每个问题记录：用例编号、Bot ID结尾、日期/关键词/结论/页码、操作、实际结果、截图。
比如：“U09，TE，9月10日至9月10日，全部、无关键词，列表应10条但实际30条。”
验收结束后告知开发者清理本批，或等待记录的自动清理时间。不要用无条件DELETE/TRUNCATE清表。


> 2026-09-11 / draft.3：两个监控 POST 不要求应用层 Token，依赖部署侧内网限制；其他模块鉴权不变。此前验收数字是历史结果，不能当作本修订已通过真库/完整 Host 联调的证明。
