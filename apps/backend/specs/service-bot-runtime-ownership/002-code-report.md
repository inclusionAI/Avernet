---
agent: tc-code
status: completed
created: 2026-10-19T22:36:00+08:00
iteration: 1
---

# 编码报告

## Worktree 信息
- 路径: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-runtime-ownership
- 分支: feat/service-bot-runtime-ownership

## 既有链路与边界
- 既有链路: BotBuildService.build 使用NAS source Home，_migrate_bot_instance 将引擎目录写入版本根；.local/bin 已位于该版本根旁。restore_draft 由版本根恢复草稿。
- 允许新增点: 聚焦.agentclaw helper及既有快照/恢复调用点。
- 禁止触碰点: 不改router/composer、引擎目录、.local/bin策略、Teclaw配置发布链及持久化结构。
- build() 当前直接调用 is_nas=True；发布物为挂载的版本目录（返回migration_path=version/engine），此链无tar/zip上传。完整.agentclaw在其父级，与既有.local/bin契约一致，OCB安装方按source_dir父级接收。

## 改动文件列表
| 文件 | 改动 | 原因 |
| --- | --- | --- |
| agentclaw_artifact.py | 新增117行独立helper | 完整目录快照、内部相对链接、重试删除与忽略规则、历史恢复合并 |
| bot_build_service.py | 仅import及两处调用，共22行 | 接入原NAS快照及草稿恢复，失败保持传播 |
| test_bot_build_service_agentclaw.py | 新增聚焦测试 | 真实rsync、错误根、外部/目标链接、忽略、重试、缺失、日志、超时owner参数 |
| test_bot_build_service_local_bin.py | 既有集成用例增加5行 | 证明真实migrate/restore调用.agentclaw helper且保留engine/Home隔离 |

## 测试执行结果
- TDD red: 新测试因agentclaw_artifact模块缺失失败；接线集成用例因未生成版本级.agentclaw失败。
- pytest 新helper+local_bin+draft_restore: 95 PASS（终轮包含owner/timeout参数测试）。
- 新helper pytest-cov: 60/60可执行行，100%，无missing lines。既有bot_build_service全文件295/697=42%，本次新增可执行接线行全部覆盖，完整CI覆盖另由编排记录；不将聚焦总覆盖47%表述为全backend覆盖率。
- 覆盖原始数据: /tmp/avernet-agentclaw-final.coverage；JSON: /tmp/avernet-agentclaw-final-cov.json；测试日志: /tmp/avernet-agentclaw-final-test.log。
- ruff 四个修改Python文件PASS，git diff --check PASS。
- 完整backend CI由主编排运行，结果归入003b/009；本报告不以聚焦测试替代完整CI。
- bot_build_service.py原2290行，当前2310行，超过1000行属既有文件；本次仅两处接线，复制策略均放独立117行helper，未扩张该大文件职责，后续按既有生命周期职责拆分。

## 日志
- service_bot.agentclaw.request/success/skip/failure及cleanup.request/success；restore.request/success/skip/failure。
- 请求含device_id或bot_id、version、source/target；完成含elapsed_ms；skip/cleanup含reason；失败含error_type并向上传播原异常。
- rsync -a不使用-v或-L，不打印文件内容、链接目标正文或认证值；failure不序列化异常stderr。使用既有_run_local_command执行参数数组，保留既有命令失败日志。
- 测试验证请求/成功/失败事件、设备/Bot与耗时、私有文件正文及外部secret不落日志；真实相对npm CLI执行成功。

## OCB 配套
独立OCB worktree实现bootloader安装与草稿/verify/online owner规则；请结合OCB独立测试/PR状态判断端到端交付，本仓PASS不代表生产部署已验证。
