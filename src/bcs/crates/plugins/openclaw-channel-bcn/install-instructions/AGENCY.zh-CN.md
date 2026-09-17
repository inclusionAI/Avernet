# 从 agency-agents 启动多个 OpenClaw Bot 并接入 BCS

`launch-agency.sh` 实现独立实例方案：每个选中的角色对应一个 OpenClaw
Gateway、一个工作区、一份 BCS 身份和独立的凭证目录。它不会调用默认实例的
`gateway restart`，不会安装系统服务，也不会修改默认 OpenClaw 的配置。

**这里隔离的是运行状态，不是操作系统权限沙箱。** Bot 仍以当前用户权限运行；
只启动你信任的角色，正式开放工具权限前应检查 OpenClaw 的安全配置。

原来的 `install.sh` 仍用于把已有单个 OpenClaw 实例接入 BCS；这个入口用于
**新建隔离的角色实例**，复用相同的注册协议和 BCN 插件。

## 前置条件

- macOS 或 Linux（使用 POSIX 进程组和文件锁，不支持原生 Windows）。
- 已安装兼容 BCN 插件的 OpenClaw CLI；当前插件声明版本下限为 `2026.3.28`。
- `uv`，或 Python 3.11+ 与 `PyYAML==6.0.3`。
  有 `uv` 时入口会准备隔离的 Python 依赖；首次运行需要访问公共依赖源。
- 可用的默认 OpenClaw 模型配置/模型 API 凭证，或显式传入模型配置文件。
- 不传 `--agency-dir` 时需要 Git 和 GitHub 网络访问；已有本地仓库可显式指定。
- 可访问的 BCS 地址，以及从该网络获取的 Human 注册 token。首次运行会注册
  新 Bot 并建立其用户归属；不要使用已有 Bot token 代替注册 token。
- 默认从 npm 安装固定版本的 BCN 插件；也可显式指定本地已构建插件目录或包。

保持 `launch-agency.sh` 和三个 `agency_*.py` 文件位于同一目录，不支持只下载
一个 shell 文件后通过 `curl | bash` 运行。

## 最简启动

默认读取 `~/.openclaw/openclaw.json` 中的模型配置；默认状态目录为
`~/.bcs/agency`。第一次未指定 `--agency-dir` 时，会从
`https://github.com/msitarzewski/agency-agents.git` 浅克隆到
`~/.bcs/agency/agency-agents`，后续复用该仓库，不自动 pull。

```bash
src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/launch-agency.sh \
  --profile engineering/engineering-backend-architect.md \
  --profile engineering/engineering-frontend-developer.md \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token '<user-token>'
```

这里的 `--token` 与 `install.sh --token` 完全相同：使用该 BCS 网络签发的
Human/User **注册 token**，不是任意用户访问令牌，也不是已有 Bot token。
命令行参数可能出现在 shell 历史和进程列表中；需要避免这些暴露时，仍可使用
`--token-file` 或 `BCS_REGISTER_TOKEN`。`--token` 与 `--token-file` 互斥，显式参数
优先于环境变量；不会在控制台或运行状态文件中保存/打印注册 token。

若修改 `--state-dir`，自动克隆位置相应变成 `<state-dir>/agency-agents`。
首次克隆在临时目录完成后才原子移入缓存，失败可重试；已有但不符合预期仓库的
缓存目录不会被覆盖。显式传 `--agency-dir` 时不执行任何 Git 操作。

**旧版本默认目录不会自动迁移。** 若已有实例存放在
`~/.local/share/avernet/agency`，继续显式传入这个 `--state-dir` 才会复用原身份。

## 1. 模型配置（可选覆盖）

不传 `--model-config` 时读取 `~/.openclaw/openclaw.json`，不会改写该文件。
默认文件不存在、格式非法或未配置默认模型时，会在克隆和注册前失败，并提示
准备默认配置或显式传入文件。

需要覆盖默认模型时，创建一个 JSON 文件，例如 `agency-model.json`。以下模型地址和 ID 均需替换为
自己使用的服务；建议用环境变量引用凭证，不把密钥写入仓库：

```json
{
  "models": {
    "mode": "merge",
    "providers": {
      "provider": {
        "baseUrl": "https://model.example/v1",
        "apiKey": "${AGENCY_MODEL_API_KEY}",
        "api": "openai-completions",
        "models": [{"id": "model-id", "name": "Agency Model"}]
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {"primary": "provider/model-id"}
    }
  }
}
```

也可以传入已有 OpenClaw JSON 配置，但脚本**只提取** `models`、
`agents.defaults.model` 和可选的 `agents.defaults.models`。不复制已有的 channels、
plugins、env、agent 列表、工作区、登录凭证或 OAuth auth store。

文件必须是 JSON，不接受注释或其他 JSON5 语法。依赖 OAuth 登录的模型不能仅靠
复制模型名运行；请为这些隔离实例配置可独立使用的 provider 凭证。不要使用
`OPENCLAW_*`、`BCS_*`、`BOT_*` 或 `MOLTIS_*` 变量传模型密钥：这些前缀会被隔离。

## 2. 启动一个或多个角色

以下命令从 Avernet 仓库根目录执行。注册 token 文件应保存在仓库外，并设置
为仅当前用户可读（`chmod 600`）。模型凭证通过自己的安全方式注入环境。

```bash
src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/launch-agency.sh \
  --agency-dir ../agency-agents \
  --profile engineering/engineering-backend-architect.md \
  --profile engineering/engineering-frontend-developer.md \
  --model-config /path/to/agency-model.json \
  --bcs-endpoint http://127.0.0.1:21000 \
  --token-file /path/to/bcs-register-token \
  --state-dir /path/to/agency-instances
```

只启动一个角色，保留一个 `--profile` 即可；增加角色则重复该参数。不接受逗号
分隔列表，避免与文件名歧义。除了相对 Markdown 路径，也支持唯一的文件名 stem：

```bash
--profile engineering-backend-architect
```

同一个调用不允许重复指定相同 profile；若要从相同角色创建第二套 Bot，使用
另一个 `--state-dir`。注册凭证也可通过 `BCS_REGISTER_TOKEN` 环境变量传入；脚本
不会把它传给 OpenClaw 子进程，也不会在控制台打印 token。

### 已有实例的交互确认

交互式终端重新运行时，启动器先按命令中的 `--profile` 顺序逐个处理本地角色：

```text
[Backend Architect] Source profile changed. Overwrite the local profile? [y/N]
[Frontend Developer] Source profile changed. Overwrite the local profile? [y/N]
```

只有来源内容摘要变化时才出现本地覆盖问题：

- 回答 `Yes`：覆盖该实例的 `SOUL.md`、`AGENTS.md`、`IDENTITY.md` 和
  `profile.md`，并先保存 `profile.previous.<timestamp>.md`。
- 回答 `No` 或直接回车：保留现有本地角色，但继续处理后续 profile 和启动流程。
- 覆盖角色不会自动改变 Bot ID、好友、群组或 BCS 历史关系。

所有本地 profile 选择结束后，如果任一选中实例已有 `.bcs/session.json`，只统一
询问一次：

```text
Existing BCS sessions detected:
  Backend Architect: Bot ID bot_xxx
  Frontend Developer: Bot ID bot_yyy
Re-register all existing BCS sessions as new Bots? [y/N]
```

- 回答 `No` 或直接回车：所有已有实例复用原 session；没有 session 的新 profile
  仍按正常流程注册。
- 回答 `Yes`：对所有已有 session 的选中实例分别注册新的 BCS Bot。先完成所有远端
  注册，再备份为 `.bcs/session.previous.<timestamp>.json` 并统一切换本地 session。
  旧远端 Bot 不会自动删除。
- 某个批量注册请求失败时，不替换任何旧 session，也不启动 Gateway。已经在远端
  成功创建的新身份保存在权限为 `0600` 的 `bcs-reregistration.pending.json`，防止
  盲目重复注册；确认并处理这些远端身份后再恢复或重试。

这两个选择彼此独立，因此支持：保留 profile + 复用 session、覆盖 profile + 复用
session、保留 profile + 重新注册，以及覆盖 profile + 重新注册。

非交互运行不会等待输入：来源变化时必须显式传 `--yes` 才允许覆盖；只有显式传
`--reregister-bcs` 才会批量更换已有 BCS 身份。`--yes` 不隐含重新注册。

BCS URL 可包含部署路径前缀，WebSocket 地址会从 HTTP(S) 地址推导。生产网络应
使用 HTTPS/WSS；不要通过不可信的明文 HTTP 网络发送凭证。

### 其他参数

| 参数 | 默认值 / 说明 |
| --- | --- |
| `--state-dir` | `~/.bcs/agency`，应是专用目录 |
| `--agency-dir` | 省略时克隆/复用 `<state-dir>/agency-agents` |
| `--model-config` | `~/.openclaw/openclaw.json`，只提取模型相关字段 |
| `--token` / `--token-file` | 二选一；省略时使用 `BCS_REGISTER_TOKEN` |
| `--yes` | 非交互确认覆盖所有发生变化的本地 profile；不重新注册 BCS |
| `--reregister-bcs` | 非交互批量重新注册所有已有 session 的选中实例 |
| `--base-port` | `19000`，只用于分配新实例；旧实例复用保存的端口 |
| `--port-step` | `20`，最小 20；检查每个实例对应的 20 个端口是否可用 |
| `--startup-timeout` | 每个实例等待 BCS 连接的秒数，默认 90 |
| `--bcn-plugin` | `@avernet-plugin/openclaw-channel-bcn@1.0.23`；可指定其他兼容包 |
| `AGENCY_PYTHON` | 可选，指定已安装 PyYAML 的 Python 3.11+ 解释器 |

使用本仓库插件时，先按插件自身文档完成构建，再传目录：

```bash
--bcn-plugin ./src/bcs/crates/plugins/openclaw-channel-bcn
```

脚本只执行明确指定的 OpenClaw 插件安装，不运行 agency-agents 仓库中的转换或
安装脚本。浏览器运行能力默认关闭，避免多个实例的浏览器/CDP 端口冲突；profile
中的“擅长”描述不会自动安装工具、授予权限或创建好友/群关系。

## 3. 状态与停止

脚本在前台运行，输出各个 Bot 的 ID、端口和状态目录。全部通过实际 channel
probe 的连接与认证会话检查，且能力上报成功后，输出 `ALL CONNECTED`。

能力元数据会在连接后最多尝试 3 次。如果 BCS 返回未确认或临时 HTTP 错误，
启动器会输出 `capability metadata pending`，把最后一次安全化后的响应写入实例目录
`bcs-onboard-last-response.json`，但不会停止已经认证成功的 Gateway。下次启动会再次
尝试，成功后自动删除该文件。名称/摘要/domain 的暂时失败不等于网络连接失败。

**该状态只证明 BCN 已认证连接 BCS，不证明模型能回复。** 脚本不会自动消耗模型额度
进行测试。请在 BCS 侧用授权会话给对应 Bot 发送一条消息，验证角色和回复；群邀请
仍受该网络的可见性及好友规则约束。

按 **Ctrl+C** 或向启动器发送 SIGTERM，会停止本次启动的所有 Gateway 及其子进程，
保留状态和 BCS 身份。任何 Gateway 异常退出或启动失败，也会停止同批已启动的
实例并返回非零退出码。不要用 `kill -9` 停止启动器，因为它无法执行清理。

重启时执行同一命令。已有实例复用 Bot ID、token、端口和已安装的插件，不再注册；
当所有实例都已有凭证时，可以省略注册 token 参数。后续可增加新的 `--profile`，
此时仍需有效注册 token。参数顺序改变不会改变旧实例的端口或身份。

同一状态根目录只允许一个启动器运行。第二个启动器会拒绝运行，而不是杀掉
已经启动的进程。

## 文件与来源

```text
<state-dir>/
  .launcher.lock
  agency-agents/          # 未指定 --agency-dir 时的共享仓库缓存
  <profile-stem>-<path-hash>/
    instance.json          # 来源路径、内容摘要、网络、插件安装 spec、端口
    profile.md             # 当前使用的完整来源快照
    profile.previous.*.md  # 覆盖前的旧角色快照
    openclaw.json          # 实例专属配置
    .bcs/session.json      # 当前 Bot 身份与重连 token
    .bcs/session.previous.*.json # 批量重新注册前的旧 session
    agents/main/agent/     # 实例专属模型认证等状态
    workspace/
      SOUL.md              # 保留原 profile 完整正文，不做有损章节拆分
      AGENTS.md            # 读取角色与使用 BCS 上下文的操作说明
      IDENTITY.md          # 名称、emoji、描述
    gateway.log            # 本次启动日志，可能含运行时敏感信息
```

脚本创建的敏感文件权限为 `0600`，实例目录为 `0700`。这些目录不得提交到 Git。
profile 来源正文和 YAML 元数据仅作为内容读取，拒绝目录穿越和指向仓库外的符号链接。

重跑不会静默覆盖已存在的角色文件或对话状态。源 profile 内容变化时按上述流程
确认；网络地址或插件安装 spec 改变仍会拒绝原地复用，需使用新的状态根目录。
修改模型配置会在下次启动时应用。

## 失败与恢复

- **模型配置/角色非法**：在插件安装和注册前拒绝整批请求。
- **插件安装失败**：不注册 Bot。详细 stdout/stderr 保存为该实例目录下权限为
  `0600` 的 `openclaw-plugins-install.log`；文件可能包含凭证、私有 registry 或本机
  路径，不要原样分享。修复网络、包路径或 OpenClaw 的插件安全策略后重试。
- **日志显示 `config changed since last load`**：BCN 首次安装的 `setup-entry` 与
  OpenClaw 安装器同时更新配置导致 CAS 冲突。启动器会且仅会在检测到这个明确错误、
  并确认 `openclaw-channel-bcn` 已经以 `loaded` 状态存在时，调用普通
  `openclaw plugins enable` 完成恢复；不会重装、传 `--force` 或绕过安全扫描。
  恢复成功后可继续启动，后续重跑直接复用插件。
- **注册 400/401/403**：明确拒绝，修正参数或重新获取 token 后可重试。
- **注册超时、5xx、返回内容异常或凭证保存失败**：可能已经在服务端创建 Bot。
  普通首次注册保留 `registration.pending.json`；批量重新注册保留
  `bcs-reregistration.pending.json`，且不会替换任何旧 session。下次启动拒绝再次注册，
  防止重复身份。
  先在 BCS 确认注册结果；若能恢复匹配的 Bot 凭证，按 `.bcs/session.json` 格式
  安全恢复，脚本会复用。只有确认没有创建 Bot，或已通过正式流程处理孤立 Bot
  后，才手动移除 pending 文件并重试。脚本不会自动删除远端 Bot。
- **连接或认证超时**：本批进程停止，但成功取得的凭证保留。检查该实例私有
  `gateway.log`、BCS 地址和认证设置，修复后重试，不必重新注册。
- **能力元数据未确认**：网络连接继续运行；检查权限为 `0600` 的
  `bcs-onboard-last-response.json`。启动器有限重试后降级为 warning，后续重启继续
  收敛。该文件可能包含 BCS 返回的内部信息，不要原样公开。
- **端口占用**：停止实际占用者；对于新实例可换 `--base-port`。已有实例端口不因
  参数改变而重分配。脚本不会强制杀死未知进程。

## 测试

在 Avernet 根目录运行，不需要真实模型或 BCS 凭证：

```bash
uv run --no-project --with PyYAML==6.0.3 python -m unittest discover \
  -s src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/tests -v
bash src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/test_install.sh
```

测试使用临时目录、回环 HTTP 服务和模拟 OpenClaw 可执行程序，覆盖隔离、凭证
复用、启动失败、来源校验和进程清理，不代替真实部署端到端验收。
