---
agent: tc-code-reviewer
status: completed
created: 2026-10-19T22:38:00+08:00
iteration: 1
---

# 代码评审报告

## 评审范围
- Worktree: /Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-runtime-ownership
- 分支: feat/service-bot-runtime-ownership
- 基准: origin/dev = 8ac9722d6b06cec9bf65253506355f315c50c498；审查当前工作区全部业务变更（含未跟踪文件），最终提交/rebase SHA由PR报告补充。
- 业务及测试文件4个；只新增Home制品helper、BotBuildService两处接线和行为断言测试，不改router/composer/存储模型。

## 固定检查维度
| 维度 | 结论 | 说明 |
| --- | --- | --- |
| 正确性 | PASS | 完整.agentclaw位于版本根，与engine平级；NAS构建和草稿恢复真实接线；旧无目录制品兼容、重试删除/忽略清理、恢复合并均有断言。 |
| 安全性 | PASS | 固定Home/.agentclaw入口拒绝链接及非目录；rsync -a不使用-L，不解引用外部链接；目标内部目录链接由真实rsync测试证明不越界。 |
| 性能 | PASS | 一次rsync同步目录；无逐文件远端调用或新增数据库访问。 |
| 代码风格 | PASS | 使用既有logger、ignore工具、命令执行器；原大文件仅import+两处调用，无职责扩张。 |
| 测试覆盖 | PASS | reviewer独立执行helper/local_bin/draft_restore三组95/95 PASS；新模块60/60=100%，本次大文件接线3/3=100%。 |
| ACI覆盖率门禁 | PENDING | 本地聚焦覆盖不能替代实际PR/job，最终base/head与远端三指标尚未产生。 |
| 静态检查 | PASS | reviewer独立对四个修改Python文件执行ruff check及git diff --check通过；未新增未用import/变量或冒号/注释格式问题。 |
| 外部系统边界日志 | PASS | 无新增HTTP/RPC接口；文件命令边界有request/success/skip/failure与Bot/device/version/source/target/error_type/elapsed_ms，既有executor提供错误明细；敏感正文不落日志有断言。 |

## 覆盖率证据
- Reviewer独立命令：apps/backend/.venv/bin/python -m pytest apps/backend/tests/community/core/service_bot/services/test_bot_build_service_agentclaw.py apps/backend/tests/community/core/service_bot/services/test_bot_build_service_local_bin.py apps/backend/tests/community/core/service_bot/services/test_bot_build_service_draft_restore.py --cov=agentclaw.community.core.service_bot.services.agentclaw_artifact --cov=agentclaw.community.core.service_bot.services.bot_build_service --cov-report=term-missing --cov-report=json:/tmp/review-runtime-cov.json -q
- 聚焦用例：95/95=100%，skipped=0，failed=0。
- 新模块：60/60=100%；既有bot_build_service全文件295/697=42.32%，两文件聚焦合计355/757=46.90%，此分母不是完整backend，更不是ACI PASS。
- 本次可执行变更行：新模块60行+bot_build_service新增import/两调用3行，共63/63=100%；未覆盖变更行：无。工作区diff新增行与coverage可执行行集合交集计算。
- 远端ACI：PENDING；casePassRate阈值100%、lineCoverage阈值70%、changeLineCoverage阈值90%，实际job分子/分母待PR阶段核验。

## Review Spec 检查项
| 编号 | 检查项 | 结论 | 说明 |
| --- | --- | --- | --- |
| R-01 | 层次/职责与extra_include_files边界 | PASS | 独立service helper，未将目录塞入单文件配置。 |
| R-02 | 完整目录与相对软链 | PASS | 真实npm风格bin→lib/node_modules链接保留且执行输出正确；外部链接无正文复制。 |
| R-03 | 重试/ignore/恢复/失败 | PASS | 删除源文件、源缺失、根/子项排除、非法根、复制失败和历史恢复均验证。 |
| R-04 | 最小权限范围与失败传播 | PASS | 本仓仅rsync恢复chown=1000:1000；不全量chown Home，异常不吞。 |
| R-05 | 静态检查 | PASS | ruff与diff --check实测通过。 |
| R-06 | 两仓交付状态分离 | PASS | OCB独立安装与阶段owner另审；本仓源码PASS不等于部署生效。 |
| R-07 | 新增/变更行为覆盖率>90% | PASS | 新helper100%，变更可执行行100%；旧大文件全文件覆盖明确单独报告。 |

## 整体结论
**结论: PASS**（本地代码审查与相关测试；远端ACI evidence PENDING）

继续完整backend回归、最终rebase和PR实际CI核验。必须保持Avernet GitHub源码PR与OCB配套PR分开；未部署预发/生产，不能声称运行环境已生效。
