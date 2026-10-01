---
agent: tc-code
status: implementation-complete-local-tests-passed
created: 2026-09-17
iteration: 1
---

# 原地重启编码报告

## 工作区

- Avernet: `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`
- Branch: `feat/service-bot-publish-ignore-ops-rel20260917`
- 保留既有 publish-ignore 去签名修改；本轮没有提交、推送、部署或操作远端 Bot。

## 已实现 Backend 入口及任务链

- `router_publish.py`: 新增 flat `POST /api/service-bot/publish/{publish_id}/restart-in-place`，复用原认证及协作者权限；固定 `in_place=True`。不新增 body、stage、version 参数。
- `restart_mixin.py`: `restart_bot`、`execute_restart`、`_recreate_restart_target` 增加缺省 false 参数。正常升级、online 主动重建和 TargetBotGoneError fallback 均透传；已有校验、状态机、幂等恢复保持不变。
- `tasks.py`: 仅原地模式的 durable payload 增加 `in_place: true`；历史缺省任务仍正常，复用 builder 的 destroy payload 保持原样。
- `bot_build_service.py`、`baas_service.py`、`deploy_config_composer.py`、`managed_composer.py`：create/release 与 upgrade 两条链完整透传；只在 true 时追加固定 `--in_place_restart true`，默认启动命令与只读规则不变。
- daas `bootstrapping/start_service.sh`：新增默认 false、命令行解析、bootstrap reexec 保留和跳过 migration 分支，仅增加16行、替换1行；`service_bot_transition.sh` 与 `set_read_only_paths.sh` 本轮未修改。
- Backend 生产代码限7文件，新增117行/删除10行；daas 生产代码限1文件。未改 `docker/agent/start_service.sh`。

## 测试及质量证据

- TDD RED: `restart_bot` / `execute_restart` 新参数4个用例因缺少参数失败；task true 模式因丢标志失败；新路由因尚未存在失败。
- 首次 GREEN: router + publish flow + task suite **248 passed**。
- 中间补充用例集 **70 passed / 1 failed**：失败来自并行接线未完成时缺少 `BotBuildService.upgrade_async(in_place=...)` 参数；接线完成后全量回归已通过，该中间失败已闭环。
- 三个新增 registry runner 真实 DI 场景已通过：Owner 请求写入真实 durable queue 的 flag，非协作者返回403，draft 发布单拒绝重启。
- 参数化验证 false/true 经正常升级、主动重建、BOT_NOT_FOUND fallback 传给最终 build 调用。
- 新路由测试覆盖成功、service 返回失败、匿名、404/400/500映射、敏感异常不落日志。
- 本任务 router/mixin/tasks 和对应测试 flake8 选择项 `E999,E902,E117,F405,E712,E701,E702,F821,F822,F823,F831,F401,F841,E203,E265` 通过；`git diff --check` 通过。
- 部署链相关测试94 passed；其中新增6个真实 async Build → BaaS → composer 测试覆盖 create/upgrade 的省略、false、true，只替换网络边界。主 agent 独立复跑6个全部通过。
- Shell TDD：实现前7 failed/2 passed，最终11 passed；相邻启动/迁移回归65 passed。独立 regression 的启动+ignore复制28 passed；主 agent 再次复跑新增11 passed、bash -n通过。
- 最终 Backend 全量：**18,815 passed、43 skipped、0 failed**，545个既有warning，耗时266.18s。总行覆盖率98,593/110,564（89.17%）。
- Review 指出的日志正向断言缺口已修复：捕获INFO并断言请求、成功、业务失败、异常、匿名拒绝事件及mode/status/duration，秘密不落日志/响应。完整router文件32 passed，主agent与regression均独立复跑。
- 独立代码Review及本地regression均PASS；本轮可计数新增可执行语句35/35（100%），详见003报告中的既有排除范围说明。提交级和远端ACI仍待后续交付验证。
- 加严E203检查发现bot_build_service.py:480/482两条既有切片空格问题，已与HEAD核对非本次引入，未扩大diff修复。阻断错误/未使用项检查和diff whitespace均通过；BaaS既有class级coverage排除未修改，真实payload测试补充覆盖行为证据。
- 中间日志：`/tmp/restart-backend-focused.log`、`/tmp/restart-focused-final.log`；最终全量日志：`/tmp/service-bot-in-place-restart-backend-full.log`，覆盖率JSON与JUnit分别为同前缀的`-coverage.json`、`-junit.xml`。本地working-tree覆盖率不等于远端ACI。

## 日志

新 HTTP 入口记录 system、direction、method、route、publish_id、operator、in_place；响应记录 stage、success、bot_uuid、耗时。匿名和异常均记录结果；异常仅记录类型及错误码，不记录可能含凭据的异常原文。已知404/400领域错误维持消息，其他异常使用通用失败消息。

## 已知边界

- 不新增运行目录/NAS检查，不禁止原有自动重建。
- 只跳过制品迁移，不改变只读规则步骤；不保证启动阶段完全无文件写入/chmod。
- Backend 和 daas 新参数必须配套发布。此报告不声明任何远端验证已通过。
- 既有大文件：router_publish.py 2059→2122行，bot_build_service.py 2078→2086行，baas_service.py 3852→3858行；其余生产Python文件均小于1000行。为遵守用户最小diff要求，本轮只做入口及参数接线，不顺带拆分既有大文件；后续独立按router操作组、build/部署职责拆分，并保留当前集成测试。此为已明确记录的既有技术债，不宣称全仓满足1000行规范。
