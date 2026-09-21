---
agent: tc-code
status: completed
created: 2026-09-21T16:50:21+08:00
iteration: 1
---

# Engine 编码报告

## Worktree

- 路径：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-file-count-rel20260922`
- 分支：`feat/service-bot-file-count-rel20260922`
- 本子任务不提交、不部署；Backend 实现与全量门禁由主 agent 统筹。

## 实现

新增 `GET /api/file/count?path=...&request_id=...`，沿现有 FILE_LIST、EngineManager.file、FileService 和 OpenClawFilePort 分发。成功只返回原始 path、普通文件目录项计数和耗时；所有失败给稳定 detail，不返回原始异常。

OpenClaw 根由现有 workspace_root().parent 派生。固定 Python worker 使用 argv 启动，不经过 Shell；从根 fd 逐段 O_DIRECTORY/O_NOFOLLOW 打开，检查目录身份与变化，不跟随符号链接、不应用 ignore、不读文件内容。隐藏文件和 ZIP 各一，硬链接按目录项计数，FIFO/目录/链接不计数。

每进程两槽，满载立即503（无排队）；十秒扫描期限。超时或取消 terminate、0.5秒宽限、必要时 kill，并等待退出后释放槽。真实进程测试覆盖忽略SIGTERM、spawn时取消及重复cancel。

Claude Code 的多根配置未定义规范主根，明确501；LocalOpenClawPluginImpl为无授权物理根的内存double，也明确501。真实本地合同通过OpenClaw生产port与临时目录执行；已同步001 Spec与Engine合同文档。

## 改动文件

| 文件（相对src/engine） | 类型 | 职责 |
| --- | --- | --- |
| src/engine/community/api/file/router.py | 修改 | 查询路由、稳定映射、结构化边界事件 |
| src/engine/community/core/file/models.py、protocol.py | 修改 | CountFilesResult及Service API |
| src/engine/community/plugin_api/openclaw/file.py | 修改 | 原生文件计数Plugin API |
| src/engine/community/core/adapters/openclaw/file.py | 修改 | 类型转换与插件调用 |
| src/engine/community/core/adapters/claude_code/file.py | 修改 | 显式unsupported |
| src/engine/community/plugins/openclaw/_file.py | 修改 | 配置根与共享scanner接入 |
| src/engine/community/local/openclaw/plugin_impl.py | 修改 | 内存double显式unsupported |
| src/engine/community/kernel/file_count.py、README.md | 新增/修改 | 中性错误、ContextVar关联与日志脱敏 |
| src/engine/community/plugins/file_count.py | 新增 | 有限并发与可取消子进程生命周期 |
| src/engine/community/plugins/file_count_worker.py | 新增 | fd约束的扫描worker |
| src/engine/community/plugins/tests/test_file_count.py | 新增 | 36项语义、边界、竞态及资源生命周期测试 |
| src/engine/community/api/tests/test_file_router.py | 修改 | 14项计数HTTP与取消日志合同测试 |
| docs/file-count-contract.md、specs/service-bot-file-count-plan.md | 新增 | 合同、边界、限制与实施计划 |

## 验证

- TDD：第一组9项真实目录测试缺少count_files而失败；新增13项路由测试因404失败；本地double失败用例后补显式unsupported。
- 相关单元/HTTP/协议回归：**143 passed**，只有已有Starlette httpx弃用警告。
- 新scanner36项测试覆盖：特殊文件/链接环/绝对相对路径/权限失败/不存在/变更/根外路径/根祖先链接/原始异常脱敏/并发与清理。
- SAST：`scripts/ci/python_sast_local.sh src/engine 1`通过，执行器为uvx flake8，使用该脚本全部block规则（FLA扩展规则依赖企业插件，公开flake8仅识别已装规则）。
- 改动文件附加F401/F841/E203/E211/E265检查通过；git diff --check通过。
- 修改及新增源文件均小于1000行；新增worker115行、scanner92行、中性契约27行，既有local plugin322行。
- 独立Engine全量回归由file_count_regression agent运行；最终全量/提交态门禁以主报告为准，本文不预报其通过。

## 覆盖率证据

局部真实执行报告：`/tmp/file-count-engine-coverage.json`。143项测试指定下列模块后的整体730条可执行行覆盖97%。

| 模块 | 行覆盖率 |
| --- | --- |
| kernel/file_count.py | 100% |
| plugins/file_count.py | 100% |
| plugins/file_count_worker.py | 99%（未纳入父进程覆盖的`__main__`入口在真实worker测试执行） |
| api/file/router.py | 91% |
| core/adapters/openclaw/file.py | 100% |
| core/adapters/claude_code/file.py | 87%（遗漏均为原有操作分支；新增unsupported已覆盖） |
| core/file/models.py、protocol.py | 100% |
| local/openclaw/plugin_impl.py | 99% |
| plugins/openclaw/_file.py | 98% |

上述已测模块的工作树改动可执行行230/232=99.14%；该数字不是提交态CI结果，未额外包含只定义Protocol的新port方法。最终由全量覆盖XML和提交范围重算，不用零diff冒充通过。

## 外部边界日志

请求/成功/失败记录独立结构化字典：system、operation、direction、engine、request_id、path、status、elapsed_ms，成功含完整响应，失败含稳定error_code及null数量。原始异常完全不记录；嵌套dict/list/tuple递归脱敏token、Authorization、Cookie、password、secret、key、credential、session及大小写变体。ContextVar把request_id传到子进程cleanup日志；取消在实际回收后记录terminal failure。测试检查结构化字段及caplog最终文本，且真实取消测试确认cleanup与failure关联同一请求。

## 风险与限制

计数是扫描期间观测值，不是原子快照。配置的根目录各级必须是真实目录，根祖先软链接被拒绝；配置应使用实际绝对路径。没有规范主根的引擎当前明确unsupported；不降级为list_dir或虚假0。
