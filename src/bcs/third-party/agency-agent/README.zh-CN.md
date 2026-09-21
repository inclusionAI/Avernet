# Agency Agent → BCS 独立启动器

选择 agency-agents 的角色或整个团队，启动隔离的本地 Agent 并接入 BCS。
当前唯一支持的引擎是 **OpenClaw**。English edition: [README.md](./README.md).

本目录自包含：`launch-agency.sh`、同目录的全部 `agency_*.py` 模块、测试和本文档可一起
复制到任意目录运行。启动器不导入 Avernet 其他 Python 包，不依赖或调用
`install-instructions/install.sh`。运行时通过公开 CLI 安装 BCN 插件并使用 BCS
HTTP/WebSocket 合同，而不是读取本仓库插件的实现。

## 前置条件

- macOS / Linux，OpenClaw CLI 在 PATH 中；BCN 1.0.23 声明的 OpenClaw 下限为
  2026.3.28。`--engine claude-code`、`--engine codex` 暂不支持，启动前会直接拒绝。
- `uv`，或 Python 3.11+ 与 `PyYAML==6.0.3`。有 uv 时自动准备隔离依赖；无 uv
  时也可用 `AGENCY_PYTHON` 指定已具备依赖的解释器。
- 已配置可用模型；默认只读 `~/.openclaw/openclaw.json` 中的模型字段。
- 首次未指定 `--agency-dir` 时需要 Git 和 GitHub 网络访问。
- 可访问的 BCS 地址；创建新 Bot 或主动重新注册时需要 Human 注册 token。

运行状态隔离**不是操作系统权限沙箱**。Agent 仍以当前用户权限运行，不要运行
不可信的角色内容。脚本不会运行 agency 仓库中的转换器/安装脚本，不会自动授予
角色文案中描述的权限、安装工具、建立好友关系或创建群组。

## 快速启动

在本目录执行（或把入口替换为脚本的完整路径）：

```bash
# 启动两个角色
./launch-agency.sh \
  --engine openclaw \
  --profile engineering/engineering-sre \
  --profile engineering/engineering-backend-architect \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token '<human-register-token>'

# 启动 engineering 团队全部角色
./launch-agency.sh \
  --engine openclaw \
  --team engineering \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/register-token

# 多个团队与单独角色混用，重叠项只启动一次
./launch-agency.sh \
  --team engineering \
  --team design \
  --profile engineering/engineering-sre \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/register-token
```

如果只想拿到脚本本体，也可以直接 curl 入口文件。它会先检查本目录内是否已有
`agency_launcher.py`；没有的话，按固定分支下载 Avernet 的 `third-party/agency-agent`
内容到 `~/.avernet/bcs/agency-agent/.bundle/`，再执行启动。这个模式适合把脚本贴到
README 里：

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/third-party/agency-agent/launch-agency.sh || echo exit\ 1)" --launch-agency.sh --engine openclaw \
  --profile engineering/engineering-sre \
  --profile engineering/engineering-backend-architect \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token '<human-register-token>'
```

注意上面的 `bash -c` 用法只是执行入口脚本；脚本会自行完成其余文件的获取。

```bash
# 启动两个角色
./launch-agency.sh \
  --engine openclaw \
  --profile engineering/engineering-sre \
  --profile engineering/engineering-backend-architect \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token '<human-register-token>'

# 启动 engineering 团队全部角色
./launch-agency.sh \
  --engine openclaw \
  --team engineering \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/register-token

# 多个团队与单独角色混用，重叠项只启动一次
./launch-agency.sh \
  --team engineering \
  --team design \
  --profile engineering/engineering-sre \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/register-token
```

`--profile` 使用 **team/profile**，即仓库相对 Markdown 路径去掉 `.md` 后缀；
仍接受 `team/profile.md`，两种写法对应同一个实例。不再支持无 team 的裸文件名。
有子目录的角色可以写成 `team/subdirectory/profile`。

`--team` 代表 agency 仓库内的顶层角色目录。按路径排序展开该目录及子目录的
Markdown 角色文件，忽略 `README.md`、`AGENTS.md`、`CONTRIBUTING.md`、`LICENSE.md`
及隐藏路径。角色文件必须符合 frontmatter 格式，否则在安装或注册前拒绝整批。
不存在、空目录、目录穿越和逃逸仓库的符号链接均被拒绝。

所有 `--profile` / `--team` 按命令行顺序解析；相同文件第一次出现的位置生效，
重复 team、profile 或两者交叉都不会创建重复实例。至少指定一个选择参数。

### 大批量启动确认

只要命令包含 `--team`，就统计全部 `--team` 与 `--profile` 合并、去重后的 agent
总数（包括已有实例，不仅是新增实例）。**总数超过 5 个**时，只统一提醒并询问一次：

```text
WARNING: selected 12 agents in total after deduplication. Launching this many agents uses more processes/memory and may incur model costs.
Continue launching all 12 agents? [y/N]
```

- `y` / `yes`：继续后续 profile 覆盖确认、统一 BCS 重新注册确认及启动流程。
- `n` / 直接回车 / 输入结束：取消本次启动，不改写任何实例，也不安装插件、注册
  Bot 或启动 Gateway。首次解析角色前可能已完成共享仓库缓存克隆。
- 总数恰好 5 个或更少不询问；只指定 `--profile` 而未使用 `--team` 也不触发此检查。
- 非交互环境下超过阈值会明确拒绝，请在交互终端确认或缩小团队选择范围；
  `--overwrite-profile` 和 `--reregister` 不能跳过这个独立确认。

整个 team 可能包含很多角色，启动会占用相应进程、内存和端口，并为新角色各注册
一个 Bot。它们在收到任务后还可能分别消耗模型额度，建议先用单个 profile 验证。

## 参数

| 参数 | 默认值 / 含义 |
| --- | --- |
| `--engine` | `openclaw`，目前只接受这个值 |
| `--profile TEAM/PROFILE` | 可重复，选择一个角色；允许 `.md` 后缀 |
| `--team TEAM` | 可重复，选择团队全部角色；与 profile 合并去重 |
| `--agency-dir` | 指定本地仓库；省略时首次克隆/之后复用 `<state-dir>/agency-agent` |
| `--state-dir` | `~/.avernet/bcs/agency-agent`，共享根目录；实例位于 `<state-dir>/<engine>` |
| `--model-config` | `~/.openclaw/openclaw.json`，只读提取模型配置 |
| `--bcs-endpoint` | 必填，HTTP(S) BCS 根地址，可带部署路径前缀 |
| `--token` / `--token-file` | 互斥；省略时使用 `BCS_REGISTER_TOKEN` |
| `--overwrite-profile` | 自动同意覆盖所有发生变化的本地 profile；**不**重新注册 BCS |
| `--reregister` | 自动同意重新注册所有已有 session 的选中实例 |
| `--parallel` | 同时执行的安装/启动任务数，默认 `4`；`1` 退回串行 |
| `--base-port` | `19000`，只为新实例分配端口 |
| `--port-step` | `20`，最小 20；预检查每个实例对应的 20 个端口 |
| `--startup-timeout` | 每个实例等待认证连接的秒数，默认 90 |
| `--bcn-plugin` | `@avernet-plugin/openclaw-channel-bcn@1.0.23`，可改用兼容版本/已构建的本地插件包 |

`--yes` 已改名为 `--overwrite-profile`，`--reregister-bcs` 已改名为
`--reregister`，旧名称会报参数错误，不会悄悄做其他操作。

Human token 与单实例安装流程使用相同注册语义，不是任意用户访问令牌，也不是
已有 Bot token。命令行 token 可能出现在 shell 历史或进程参数中；需要避免时使用
权限为 `0600` 的文件或环境变量。脚本不把注册 token 传给 OpenClaw/Git 子进程，
也不在控制台打印。生产环境请用 HTTPS/WSS。

## 并行启动与彩色日志

默认同时处理最多 **4 个** agent 的准备/插件安装任务。所有插件准备成功后，才按
选择顺序串行注册 BCS、处理批量 session 切换。随后最多 4 个 agent 同时启动 Gateway、
等待认证连接并上报能力。全部完成后才输出 `ALL CONNECTED`。

```bash
# 2 个启动任务并发；适用于希望降低启动时资源占用的机器
./launch-agency.sh --team engineering --parallel 2 \
  --bcs-endpoint http://127.0.0.1:21000 --token-file /path/to/register-token

# 排查时退回串行准备和连接检查
./launch-agency.sh --team engineering --parallel 1 \
  --bcs-endpoint http://127.0.0.1:21000 --token-file /path/to/register-token
```

`--parallel` 限制的是**正在安装/启动的任务数**，不是最终在线 agent 的总数。
例如选中 12 个角色、并发为 4，最终仍会有 12 个独立 Gateway 运行。所有交互
（大团队确认、profile 覆盖、统一重新注册确认）均在并行任务开始前串行完成。

每条 agent 进度包含名称；任务完成顺序可能不同于参数顺序，后完成的任务不会
阻塞其他任务的进度提示。终端中使用蓝色进度、绿色成功、黄色警告/确认和红色
错误；重定向或管道输出自动用纯文本，也可设置 `NO_COLOR=1` 禁用颜色。
颜色只用于人类可读的输出，不加入 JSON/私有诊断文件。

任一必要阶段失败或 Ctrl+C 时，会取消尚未开始的任务，终止并回收本次创建的
命令进程组/Gateway；不会继续注册或遗漏后台安装进程。已开始的能力上报 HTTP
请求受原有 20 秒超时限制，清理会等待请求结束，不在后台遗留工作线程。能力上报
pending 仍是警告，不会让其余已认证连接失败。

## 持久化目录与复用

默认目录结构：

```text
~/.avernet/
  bcs/
    agency-agent/
      agency-agent/                         # agency-agents Git 仓库缓存，共享只读来源
      openclaw/
        .launcher.lock                      # 仅限制同一引擎的启动器
        engineering-sre-<stable-path-hash>/ # OpenClaw 独立实例
        engineering-backend-architect-<hash>/
      codex/                                # 引擎布局预留，当前不支持启动 Codex
        ...                                 # 不被 OpenClaw 操作扫描或修改
```

首次从 `https://github.com/msitarzewski/agency-agents.git` 浅克隆仓库，之后复用，
不自动 pull。克隆先写临时目录，成功后才放入固定位置；失败不会保留半成品仓库。
显式指定 `--agency-dir` 时不执行 Git 操作，也不复制一份新仓库。

实例目录名由**规范化后的仓库相对路径**决定，保留原有稳定的路径 hash 算法，
不使用随机数、时间戳、内容 hash 或选择顺序作为目录名。**引擎由父目录隔离**，
同一个 profile 在不同 engine 下的角色、session、历史和运行配置互不共享。以下操作
在同一引擎下都复用同一个实例目录、Bot ID、session、端口和记忆：

- 重复执行相同命令；
- 在 `team/profile` 与 `team/profile.md` 两种写法之间切换；
- 先启动一个 profile，之后启动包含它的 team；
- 改变团队/角色的选择顺序。

只有新选中的角色才创建新实例目录；只有明确确认覆盖或重新注册时才生成相应
备份文件。普通重跑不新增实例目录或角色/session 备份。不自动复制全量仓库到每个
实例；每个实例只保存自己选中的 profile 内容。

实例内包含：

```text
instance.json                         # profile 来源摘要、engine、网络、插件、端口
profile.md                            # 当前采用的完整角色来源快照
profile.previous.<timestamp>.md       # 明确覆盖前的旧来源快照
openclaw.json                         # 实例配置
.bcs/session.json                     # Bot 身份和重连凭证
.bcs/session.previous.<timestamp>.json # 明确重新注册前的旧身份
agents/main/agent/                    # 模型认证等独立运行状态
workspace/SOUL.md                     # 完整角色正文
workspace/AGENTS.md                   # 操作说明
workspace/IDENTITY.md                 # 名称、emoji、简介
workspace/MEMORY.md                   # 如运行中生成则一直保留
gateway.log                          # 本次 Gateway 日志
```

配置、凭证、诊断和备份由脚本以 `0600` 写入，实例目录为 `0700`，不要提交到 Git。
这不是“完全不写文件”：正常重启仍更新运行配置/日志，Gateway 可能更新重连凭证。

## 覆盖与 BCS 身份确认

所有 profile 覆盖、session 检查和 `--reregister` 都只针对**当前 `--engine`**
目录内本次选中的实例，不扫描、不修改其他引擎（即使 profile 名称完全相同）。

交互式终端中，先逐个询问发生变化的 profile 是否覆盖本地角色。No 保留原快照和
角色文件并继续，Yes 备份旧快照再覆盖 SOUL/AGENTS/IDENTITY。覆盖不会清空对话、
记忆或模型认证目录。

所有本地选择完成后，只统一询问一次：

```text
Existing BCS sessions detected:
  SRE: Bot ID bot_xxx
  Backend Architect: Bot ID bot_yyy
Re-register all existing BCS sessions as new Bots? [y/N]
```

No 复用所有旧身份；Yes 需要 Human token，为所有已有 session 的选中实例创建
新身份。新角色没有 session 时照常注册。旧远端 Bot 不自动删除。
`--overwrite-profile` 与 `--reregister` 分别跳过对应询问，相互独立。
非交互下默认复用 session，发生 profile 内容变化但未传 `--overwrite-profile` 则
明确拒绝，不阻塞等待输入。

批量重新注册时先获取全部新凭证，再切换本地 session。如果其中一个远端请求
失败，原 session 尚未替换，不启动 Gateway，已成功创建的新身份保存在私有
`bcs-reregistration.pending.json` 中，供人工核对恢复，禁止盲目再次注册。

## 模型与运行状态

模型配置必须是 JSON，默认从 `~/.openclaw/openclaw.json` 只提取 `models`、
`agents.defaults.model` 和可选的 `agents.defaults.models`。不复制其他 channels、
plugins、env、agent 列表、工作区或 OAuth auth store，也不改写默认 OpenClaw 配置。

若 provider 依赖 `${MODEL_API_KEY}` 等引用，需要自行在启动环境中提供；默认
OpenClaw 的 `.env` 或 OAuth 登录不会自动继承。`OPENCLAW_*`、`BCS_*`、`BOT_*`、
`MOLTIS_*` 前缀的环境变量会被隔离，不要用这些前缀提供模型密钥。

脚本前台监督所有实例，Ctrl+C/SIGTERM 只停止本次启动的 Gateway，并保留状态。
不要用 SIGKILL：此时无法保证执行清理。同一引擎目录的第二个启动器会直接
拒绝运行，不会强行停止正在运行的实例；其他引擎的运行锁不会阻塞当前引擎。
共享仓库初始化使用短期根目录锁，不持有跨引擎的长期运行锁。浏览器能力默认关闭，避免 CDP 端口冲突。

`ALL CONNECTED` 要求各个 BCN channel 已连接且取得认证 session token，**不表示
模型已能回复**。能力元数据上报失败会有限重试，之后显示 `capability metadata
pending` 并保留私有诊断，但不停止已认证连接。此脚本不自动调用模型测试，需在
BCS 侧实际发一条任务验证角色回复。

## 旧目录与旧命令迁移

旧命令须更新为 `--overwrite-profile`、`--reregister` 和 `team/profile`。
`--state-dir` 现在指**共享根目录**，而不是直接存放实例的引擎目录；即使显式传入，
也会使用 `<state-dir>/<engine>/`。

本目录不提供自动迁移脚本。检测到旧平铺实例目录时会停止启动，避免误创建重复
BCS Bot；请将原来的实例目录复制/移动到新目录结构中对应 engine 下（例如
`~/.bcs/agency/<agent>` → `~/.avernet/bcs/agency-agent/openclaw/<agent>`），并先
核对 `session.json`、端口和配置无误后再启动。可以先用 `git status`/文件备份确认
有回退路径；不要在同一身份的新旧副本同时运行。脚本不会删除旧数据。

缺少 engine 字段的旧 `instance.json` 按 OpenClaw 解释；已有相同名称的新目录时
不要覆盖。改变 BCS 网络、插件 spec 或已有实例引擎仍会拒绝启动，避免误用身份。

## 故障恢复与验证

- OpenClaw 命令失败：查看实例的 `openclaw-plugins-*.log`（可能含内部信息，勿原样分享）。
- BCN 首次安装导致 `config changed since last load`：只在插件已正常 loaded 时用
  普通 `plugins enable` 恢复；不使用 `--force` 或跳过安全扫描。
- 注册超时/5xx：保留 `registration.pending.json`（重新注册则为
  `bcs-reregistration.pending.json`），先核对远端结果再恢复，避免重复 Bot。
- 端口占用：停止实际占用者；新实例可以调整 `--base-port`。不会杀未知进程。

本目录自身的测试（无需真实 BCS / 模型 / GitHub）：

```bash
uv run --no-project --with PyYAML==6.0.3 python -m unittest discover -s tests -v
```

测试仅使用临时 HOME、回环 HTTP、模拟 OpenClaw/Git，包含将整个目录复制到
Avernet 外运行的验证。它们不替代真实模型和网络部署端到端验收。
