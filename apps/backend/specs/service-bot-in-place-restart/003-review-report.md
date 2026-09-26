---
agent: tc-code-reviewer
status: completed
created: 2026-09-17
iteration: 1
---

# 原地重启代码评审

## 范围与结论

**PASS（本地代码评审）；远端 ACI evidence PENDING。**

- Avernet worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-publish-ignore-ops-rel20260917`。
- 分支：`feat/service-bot-publish-ignore-ops-rel20260917`；当前 HEAD `5cfd442f466a2ac2fdc341d183d50ba192a36d7a`，本轮功能尚未提交。
- 逐项审查本轮 7 个 Backend 生产文件及对应测试、daas `bootstrapping/start_service.sh` 和 `tests/test_start_service_in_place_restart.py`。使用 working-tree diff，避免遗漏未提交改动及 untracked 测试。
- 既有 publish-ignore 去签名、ignore 迁移逻辑、打包文件及其他工作区修改不属于此次评审范围。

## 固定检查维度

| 维度 | 结论 | 证据 |
| --- | --- | --- |
| 正确性 | PASS | 新路由固定 true；普通调用和旧 durable payload 缺省 false；升级、主动 recreate、TargetBotGoneError fallback 均传递标志 |
| 安全性 | PASS | 复用原认证与 CollaboratorPermissionInterceptor；命令只增加固定 bool 字符串；异常日志只记录类型，测试证实凭据样本不进入日志或响应 |
| 性能 | PASS | Shell 原地模式不执行整个迁移脚本；不增加额外 IO 扫描或目录检查；实际生产耗时未测量 |
| 架构/风格 | PASS（存量债单列） | 平放现有 router，领域决策仍在原 service；无新 child router、跨层依赖或无关重构 |
| 行为测试 | PASS | 独立执行 router/tasks 70 例、publish-flow + 真实部署 payload 189 例、Shell 11 例；日志测试补强后独立重跑 router 32 例通过 |
| 静态检查 | PASS（新增行） | flake8 核心错误/unused 项通过，git diff --check、bash -n 通过；扩展 E203 检出 Build 原有两处，HEAD 内容相同，不是本轮新增 |
| 边界诊断 | PASS（本轮边界） | 新入口 request/response/failure 含发布单关联、模式、结果/异常类型和耗时；正向 INFO 捕获与失败不泄密断言已补齐；Build 日志补模式，Shell 有跳过迁移事件；沿用旧 BaaS transport |
| ACI 门禁 | PENDING | 尚无包含本轮修改的 committed head 或远端 job；本地指标不是远端 ACI 结论 |

## Review Spec 对照

| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R-01 | 同权限、复用原服务 | PASS | 真正 DI endpoint 场景覆盖 owner 入队、无权限 403、draft 拒绝；新入口不增加 stage/version/body |
| R-02 | 默认兼容及 durable 标志 | PASS | builder 仅 true 增加字段；旧任务/false 仍默认普通重启；destroy 共用 payload 不改变；既有 redelivery 语义保留 |
| R-03 | 两条 recreate 路径不丢标志 | PASS | 主动重建与目标丢失 fallback 有参数化行为断言，最终 release_async/upgrade_async → BaaS → composer 的真实 payload 测试覆盖缺省/false/true |
| R-04 | 固定启动参数 | PASS | `--in_place_restart true` 仅开启时添加，未引入用户可控命令片段 |
| R-05 | Shell 解析与 reexec | PASS | 实际 Bash 区块执行测试：缺省、true、false、缺值及 bootstrap 重解析；不是只检查源码字符串 |
| R-06 | 跳过迁移、保留只读 | PASS | 原地模式有/无 source 均不调用 transition；普通模式成功和失败退出保持；只读 stub 在应执行路径确实执行 |
| R-07 | 不新增用户否决的检查 | PASS | 无 NAS/home/安装目录校验，无禁止 recreate；只读权限策略与 docker/agent/start_service.sh 不变 |
| R-08 | 日志与失败可观察性 | PASS | 首审发现 caplog 仅否定秘密不足；已补 request、成功、业务 false、异常、匿名分支的正向断言并复测通过 |

## 本地覆盖率证据与 ACI 边界

- 全量回归产物：`/tmp/service-bot-in-place-restart-backend-full.log`、`/tmp/service-bot-in-place-restart-junit.xml`、`/tmp/service-bot-in-place-restart-coverage.json`。
- 全量后端：**18815/18815 = 100%** 已执行用例通过，43 skipped，0 failed，545 warnings，266.18 秒。跳过数单列，不冒充执行成功。
- coverage JSON 总行：**98593/110564 = 89.1728%**（阈值 70%）。
- Reviewer 独立解析上述 7 个生产路径 `git diff HEAD --unified=0` 新行号，与 coverage executed/missing 行相交：**35/35 = 100%**（候选变更行阈值 90%），未覆盖变更可执行行为 0。

| 文件 | 命中的变更可执行行 |
| --- | --- |
| router_publish.py | 24/24 |
| restart_mixin.py | 1/1 |
| tasks.py | 7/7 |
| deploy_config_composer.py | 1/1 |
| managed_composer.py | 2/2 |
| bot_build_service.py | 0/0（新增形参、日志续行、kwargs 续行不是独立 statement） |
| baas_service.py | 0/0（既有 class no-cover 排除新增 6 行） |

未修改或新增任何覆盖率排除。BaaS 排除行 665、742、924、995、2918、2981 由真实 Build/BaaS/composer 串联 payload 行为测试补充，不把它们宣称为覆盖率已命中。

目标分支本地引用 `github/REL20260917` 为 `fb15a95e1047b718bb5d13cbf4a5874f7fcc3c98`；未获取远端新状态。此次功能未提交，故不能用该引用与旧 HEAD 的报告替代真实功能 ACI，未运行针对错误 base/head 的 report_check。**远端 ACI job：PENDING。**

## 非阻塞边界与交付要求

1. Backend 和 daas 脚本须配套发布，旧脚本不认识新 flag；此评审没有部署或重启真实 Bot。
2. 跳过的是制品迁移，不是整个启动流程；只读路径权限设置和配置初始化仍存在。运行目录不可复用时仍按用户确认的不新增检查策略执行；自动重建保留原行为。
3. Managed composer 启动路径是本次生效范围；不能将此结论扩展为所有独立 runtime 的启动实现均支持原地模式。
4. Router/Build/BaaS 既有大文件与 Build 第480/482行 E203 为存量债，未混入拆分/格式化。BaaS transport 原有 response 日志未在此次扩展敏感字段；通用 transport 日志治理不作为本轮附带改动。

无待修复的本轮阻塞问题。下一步由主编排汇总本地回归，用户审核后才进入配套交付及远端 ACI 门禁；本报告不授权部署。
