# Spec — 存储行业尽调 Task 端到端 Singlebox 集成用例

> 关联源 case:`docs/2026-08-03-task-impl-briefing.md`(任务领域模型 + 全 HTTP API 调用链路)。
> 日期:2026-08-04。

## 背景与动机 (WHY)

任务内核(`community/core/task/`)已经把"存储行业尽调"case 的 13 个 HTTP 端点 + 1 WS 全链路在
briefing 文档里推演清楚了,但当前仓库:

- task 端点**没有端到端集成测试**(`coverage_baseline.txt` 里 12 个端点全标 `missing`);
- owner-bot 的 `task-recognition/clarify/plan/exec/goal-verify` skill 与执行 bot 的
  `task-exec-skill` **只存在于 briefing 概念里,没有真正的 `SKILL.md` 落地**;
- `dispatch_bot_task` / `dispatch_group_task` 高层派发封装、`GroupDiscoverService.search_by_keyword`
  **未实现**;
- 没有一条"通过 singlebox 拉起真实 bot/协作群 → 自驱走完任务全生命周期"的自动化链路。

结果:任务内核的运行态行为(搜推→路由→分发→回投→聚合→终验→reroute loop)只能在单测里被片段
验证,真实的 bot 经 BCN 回投、真实协作群动态拉建、真实 reroute 补做,都没有可回归的自动化保障。

## 目标 (WHAT)

构建一条**基于 singlebox 的自动化端到端集成用例**,以"存储行业尽调"为实例,拉通:

1. **测试 bot 真实创建**:owner-bot + 若干执行 bot,经 `POST /api/bots` 真实建档 + BCS 真实连网。
2. **skill 真实开发并安装**:owner-bot 的 recognition/clarify/plan/exec/goal-verify、执行 bot 的
   task-exec,开发为 `SKILL.md` 并经 openclaw 配置真实装入指定测试 bot。
3. **真实执行链路**:集成用例发消息给 owner-bot → owner-bot **自驱**调 task HTTP API
   (create/clarify/start) → 系统搜推/分解/分发 → 真实单 bot 执行 + 真实**动态协作群**拉建 +
   群 master 聚合回投 → BBS 上升 → 终验 FAIL → reroute 补做 → 二次终验 PASS。
4. **可回归**:跑在 singlebox 编排下,CI 可一键触发,失败有明确断言点。

## 范围

**在范围内**:
- owner-bot / 执行 bot 的 singlebox profile 与 `SKILL.md` skill 设计与安装路径。
- 缺失的派发封装(`dispatch_bot_task` / `dispatch_group_task`)与 `GroupDiscoverService` 的接口契约。
- 全链路每一步的真实 API、入参、出参、发起方、领域落点。
- 完整执行链路交互流程图。
- 自动化集成用例的骨架设计与 singlebox 编排接入点。

**不在范围内**:
- task 内核领域模型本身的设计变更(已在 briefing 定型)。
- 生产环境部署 / 云上 BCS Fuse 真实推荐(本地 singlebox 用关键词 cover + 本地 bot catalog)。
- 前端副屏画布渲染(集成用例只校验后端 API 与图状态)。

## 成功标准

- 一条命令(`./scripts/singlebox.sh ...` 或 pytest marker)拉起完整链路,跑完"存储行业尽调"
  到 `status=done`,中间经历至少 1 次 SINGLE_BOT 派发 + 1 次动态 COOP_GROUP + 1 次 BBS +
  1 次 goal FAIL reroute。
- owner-bot 在收到聊天消息后**自己**调 task API,而非测试脚本代替。
- 每个回投点(assert/state/node accepted/goal verdict)有断言。