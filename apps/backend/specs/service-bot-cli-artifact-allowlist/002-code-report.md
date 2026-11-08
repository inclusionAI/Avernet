---
agent: tc-code
status: completed
created: 2026-10-20
iteration: 1
---

# 编码报告

## Worktree
- 路径：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-cli-artifact-allowlist`
- 分支：`feat/service-bot-cli-artifact-allowlist`
- 未提交、未推送、未部署；没有写入名单数据库。

## 实现
- `runtime_artifact_policy.py` 每次build读取一次名单决策：运行env下先查`*`，再查源Bot owner，默认关闭；空owner不查询；数据库异常fail closed。不改变repository精确参数化exists语义。
- BotBuildService必需注入UserListRepositoryProtocol，composition root显式提供；迁移默认关闭，两个同步helper显式接收build固定决策；底层独立helper默认开启保持原调用兼容。
- 关闭helper不验证/遍历源CLI目录，目标存在时使用既有安全空源rsync收敛；目标不存在时不创建；目标固定父目录和链接校验继续生效。关闭时不会改草稿、引擎及其他Home数据。
- `server_managed.py`声明服务端专用名单type与领域异常。UserListService拒绝该type全部correct写入；HTTP adapter映射403；既有类型语义不变。
- README声明新依赖、名单操作SQL示例和已有发布物/容器不受名单关闭影响的兼容性。
- 更新所有手工构造器和__new__构建fixture，使用必需依赖和显式false repository结果；无optional/getattr兜底。

## 验证
- 红测：新policy尚未实现时ModuleNotFoundError；两个helper enabled行为测试6 failed，报不识别enabled参数（`/tmp/cli-gate-red.log`）。
- 绿测：相关build、user_list、repository、端点、既有DI all-bindings、模块边界/插件隔离/API层架构测试 **411 passed**，16.88s（`/tmp/cli-gate-final-tests.log`）。
- 首轮扩大验证发现两个测试使用Mock而新fixture误用未导入MagicMock，已改回Mock；非生产代码问题。
- 最后新增真实DI+SQLite隔离验证、migration默认关闭验证，执行结果追加于下。
- ruff修改Python及新增源代码/单测PASS；`git diff --check` PASS。
- 没有执行整仓重型回归；父agent负责独立review和完整后端CI。

## 排查日志
- `service_bot.runtime_artifacts.request/success/failure`包含bot_id、owner entity_id、env、type、enabled、match、elapsed_ms；异常只记录error_type，不记录异常原文和凭据。
- match覆盖global/user/none/error/missing_owner；global短路第二次查询。helper关闭skip/cleanup日志reason=feature_disabled；清理失败传播。
- correct拒绝日志含actor/entity/type/env和reason=server_managed。
- 单测验证成功、两条DB失败路径、凭据不落日志、无源目录探测、同版本开关重试、清理失败、公共端点403。

## 文件长度与最小接线
- 新policy 41行；server_managed 7行；agentclaw_artifact 120行；local_bin_artifact 230行；user_list service 74行；protocol 36行；router 362行；DI service_bot_module 861行，均低于1000。
- `bot_build_service.py`既有2310行，本次仅新增11行（import、必需DI、固定决策和helper参数），现2321行。领域逻辑已提取至独立policy；本任务不作无关重构或新增CI豁免。父agent在最终CI确认既有大文件门禁基线。

## 最终补充验证
真实composition root的BotBuildServiceProtocol→UserListRepositoryProtocol同一实例，SQLite实际同type但不同env的`*`隔离、pre全量开启，以及migration默认关闭与既有local_bin测试：**47 passed**，4.14s（`/tmp/cli-gate-last.log`）。DI测试显式创建所需名单表，避免将缺表异常fail closed误认作空名单证据。新增后ruff和diff whitespace检查均PASS。
