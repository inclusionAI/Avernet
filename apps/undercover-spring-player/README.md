# Undercover Spring Player

完整接入、启动和排错步骤见 [接入笔记](INTEGRATION-NOTES.md)。首次接入请直接按该笔记执行，
不要只照本文件的单组件示例启动。

Spring AI 驱动的“谁是卧底”玩家。它通过 BCN V3 WebSocket 连接 Avernet 的 BCS，
接收 `chat.send` 任务并流式调用 OpenAI 兼容模型。最终发言和投票会经过本地规则校验：

- 发言不超过 25 个 Unicode 字符，且不会说出自己的词或词中连续两个字。
- 投票只接受 `我投N号` 或 `我弃权`；PK 局禁止弃权、自投和非候选票。
- 收到 `chat.abort` 时，程序会终止对应 run 并直接取消模型流，不需要人工处理。

本文档按“配置、启动、验证”顺序说明如何在本地接入 BCS。

完整协议边界、规则移植和踩坑记录见 [接入笔记](INTEGRATION-NOTES.md)；三局脱敏对局记录见
[三局对局日志](evidence/three-games.md)。

## 1. 环境与配置

### 1.1 前置环境

开始前确认本机具备：

- **JDK 17**：`java -version` 应显示 17 或兼容版本。
- **Maven 3.9+**：`mvn -version` 应能正常输出。
- **已运行的 Avernet BCS**：本程序不会自行启动游戏服务端。
- **可用的 OpenAI 兼容模型接口**：需要 API Key；如果使用兼容代理，还需要对应的 Base URL 和模型名。

所有命令都从仓库根目录开始，先进入应用目录：

```bash
cd apps/undercover-spring-player
```

### 1.2 环境变量

| 变量 | 是否必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | 是 | 无 | 模型接口的 API Key，禁止写进 `application.yml` 或提交到 Git。 |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com` | 便于阅读的 Base URL 别名。当前应用不直接读取它，启动示例会把它映射给 `SPRING_AI_OPENAI_BASE_URL`。 |
| `SPRING_AI_OPENAI_BASE_URL` | 否 | Spring AI 默认值 | Spring AI 实际读取的 OpenAI 兼容接口地址。使用官方接口时可省略。 |
| `OPENAI_MODEL` | 否 | `gpt-4o-mini` | 模型名称，必须与你的接口提供方一致。 |
| `BCN_URL` | 否 | `ws://127.0.0.1:21000/ws/bot` | BCS 的 Bot WebSocket 地址。仓库 singlebox 的 BCS 默认端口是 `21000`，以实际监听端口为准。 |
| `BCN_BOT_ID` | 否 | 空 | 指定 Bot ID。为空时使用 `.bcs/session.json` 中保存的 Bot ID。 |
| `BCN_TOKEN` | 否 | 空 | 指定 BCN token。为空时优先使用会话文件中保存的 token。 |
| `BCN_CLIENT_KIND` | 否 | `custom-engine` | 握手时声明的客户端类型，除非服务端要求，否则不要修改。 |
| `BCN_SESSION_FILE` | 否 | `.bcs/session.json` | BCN token 和 Bot ID 的本地会话文件，不能提交到 Git。 |
| `BCN_HEARTBEAT_INTERVAL` | 否 | `60s` | WebSocket 心跳间隔。 |
| `BCN_CONNECT_TIMEOUT` | 否 | `30s` | WebSocket 建连和握手超时。 |
| `BCN_RECONNECT_INITIAL` | 否 | `1s` | 断线重连的初始等待时间。 |
| `BCN_RECONNECT_MAX` | 否 | `30s` | 指数退避重连的最大等待时间。 |

推荐在启动前统一导出变量。下面的示例使用官方 OpenAI 地址；接入兼容服务时替换
`OPENAI_BASE_URL` 和 `OPENAI_MODEL` 即可：

```bash
export OPENAI_API_KEY="<your-api-key>"

export OPENAI_BASE_URL="https://api.openai.com"
export SPRING_AI_OPENAI_BASE_URL="$OPENAI_BASE_URL"
export OPENAI_MODEL="gpt-4o-mini"

export BCN_URL="ws://127.0.0.1:21000/ws/bot"
export BCN_SESSION_FILE=".bcs/session.json"
```

如果 BCS 监听了其他端口，直接覆盖 `BCN_URL` 即可，例如：

```bash
export BCN_URL="ws://127.0.0.1:18080/ws/bot"
```

`BCN_BOT_ID` 和 `BCN_TOKEN` 通常不用手填：首次握手成功后，程序会把服务端返回的
token 和 Bot ID 写入 `BCN_SESSION_FILE`。如果你已经有一个明确的 Bot 身份，也可以
显式导出 `BCN_BOT_ID` 和 `BCN_TOKEN`。

不要把 API Key、token 或 `.bcs/session.json` 提交到 Git。

## 2. 启动指南

### 2.1 先启动 BCS

本程序依赖 BCS 提供 `/ws/bot` 入口。启动本程序前，先确保 Avernet 游戏服务端已经在
运行，并且该 Bot 已在 BCS 侧创建或 onboard。

如果使用仓库的 singlebox，先启动完整服务链和混合牌桌 profile：

```bash
cd ../../
./singlebox/singlebox.sh start baas
./singlebox/singlebox.sh start bcs
./singlebox/singlebox.sh start backend frontend
./singlebox/singlebox.sh start bots \
  --profile-dir singlebox/agents/my_java_undercover_profile
```

默认健康检查地址通常是 `http://127.0.0.1:21000/health`；如果你改了端口，请同步修改：

```bash
curl --noproxy '*' -fsS "http://127.0.0.1:21000/health"
```

健康检查失败时，先不要启动玩家程序，否则它只会不断重连。

### 2.2 编译

回到应用目录，执行完整编译和测试：

```bash
cd apps/undercover-spring-player
mvn clean package
```

构建成功后会生成：

```text
target/undercover-spring-player-0.0.1-SNAPSHOT.jar
```

### 2.3 启动

开发时可直接用 Maven 启动：

```bash
mvn spring-boot:run
```

也可以用打包后的 JAR：

```bash
java -jar target/undercover-spring-player-0.0.1-SNAPSHOT.jar
```

环境变量必须在启动它的同一个终端中导出。若要更换端口、Bot 或模型，不改配置文件，
直接覆盖对应变量后重启即可。

### 2.4 开始一局游戏

保持玩家进程运行，然后在 Avernet 中创建或进入“谁是卧底”对局，并把该玩家的座位绑定到
当前 Bot。BCS 派发任务后，本程序会：

1. 收到 `chat.send` 并立即 ACK，返回本次 `run_id`。
2. 流式调用模型，在内存中累积完整输出。
3. 做规则校验和兜底，然后发送一条合法的 `chat` final 事件。

## 3. 验证与排错

### 3.1 启动成功的标志

控制台首先出现 Spring Boot 启动日志，例如：

```text
Started UndercoverPlayerApplication in 0.739 seconds
```

随后应看到 BCN 握手成功日志，关键是这一行：

```text
Connected to BCN at ws://127.0.0.1:21000/ws/bot as bot <bot-uuid>
```

看到 `Connected to BCN` 才表示 WebSocket 握手、V3 能力协商和会话保存都已成功。此时
`BCN_SESSION_FILE` 指向的文件应已生成，并包含 token。

如果没有这一行，而是持续出现 `BCN connection lost`，说明连接或握手失败，先按下一节排错。

### 3.2 常见问题

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| `Connection refused` | BCS 未启动、端口不对或只监听了其他地址。 | 运行 `curl` 健康检查，确认 `BCN_URL` 和实际 BCS 端口一致。 |
| 持续重连但没有 `Connected to BCN` | WebSocket 地址可达，但握手没有完成。 | 检查 BCS 是否为 V3、是否启用 `unified_run_events` 和 `canonical_session_id`，并查看服务端日志。 |
| `BCN handshake timed out` | 网络、代理或服务端未及时响应。 | 确认 `BCN_URL`、防火墙和代理设置；必要时增大 `BCN_CONNECT_TIMEOUT`。 |
| `UNSUPPORTED_PROTOCOL_VERSION` | 服务端没有协商到协议 V3。 | 使用支持 BCN V3 的 BCS 版本，并确认入口是 `/ws/bot`。 |
| `already_connected` | 同一个 Bot 已经有另一条活跃连接。 | 关闭重复启动的玩家进程，等待旧连接断开后重试。 |
| 模型返回 `401` 或连接失败 | API Key、Base URL 或模型名不正确。 | 检查 `OPENAI_API_KEY`、`SPRING_AI_OPENAI_BASE_URL` 和 `OPENAI_MODEL`；客户端会记录告警并使用规则兜底，不影响本局继续。 |
| `RUN_NOT_FOUND` 或 `SCOPE_MISMATCH` | 中止请求指向了过期 run 或错误会话。 | 通常由服务端重试或旧请求导致；重启玩家进程后重新开局验证。 |

### 3.3 关于 `chat.abort`

游戏中途收到 `chat.abort` 时，程序会自动标记对应 run 为终态、取消正在进行的模型订阅，
并屏蔽迟到的 final/error 事件。不需要人工重启或清理，也不会把已中止 run 的回复发出去。

模型调用失败（包括限流、超时、鉴权或服务端错误）时，客户端会记录一条 `MODEL_ERROR`
告警并立即使用规则兜底回复，不会让当前 run 卡住或抛出未处理异常。

如果只想确认进程是否还活着，不需要查看每条消息日志；只要能持续收到新的对局任务并产生
合法 final，就说明连接和生成链路正常。
