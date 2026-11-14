# MLX「谁是卧底」玩家 Bot（Apple MLX 本地端侧模型引擎）

Avernet #2611 的玩家 Bot：把本机 MLX 微调模型（Ling-3.0-tiny + LoRA，见
ling-cookbook `guide/finetuning/ling-3.0-tiny/undercover-player/`）接到
「谁是卧底」牌桌，与云端等其他引擎的 agent 同桌竞技。

**要把这个玩家接进你自己的牌桌？** → [INTEGRATION.md](INTEGRATION.md)（30 分钟接入笔记，
含换引擎实现要点）。全 Bot 对局没有 LLM 裁判时，可选用脚本裁判 `referee_driver.py`
（SKILL.md 阶段机 S0–S5 的确定性实现，uc 事实层仍复用官方 `undercover.py`）。

## 架构（三层分离）

| 层 | 文件 | 职责 |
|---|---|---|
| 协议层 | `bcn_client.py` | BCN V3 WebSocket：`bot.connect` 握手（token 重连）、`bot.status` 心跳、`chat.send`（ACK run_id=请求帧 id → chat final 事件）、`chat.inject`（仅 ACK）、`chat.abort`（run_id 精确取消 + 迟到事件屏蔽）、`chat.history`、指数退避重连 |
| 游戏逻辑层 | `player.py` | 解析 `[State Machine Task]` 节点任务（speak / vote / 投票预备确认），映射为训练同款提示词，调模型，输出前规则清洗 |
| 安全层 | `rules.py` | 强制 25 字上限、泄词字删除、投票格式正则兜底、模板残渣剥离——限定输出格式，并过滤已知非法动作；不能保证任意损坏输入下合法 |

`main.py` 组装三层；模型侧走 OpenAI 兼容接口（`mlx_lm.server` 或 vLLM 均可），
换引擎只需换 `--base-url`。

## 运行

```bash
# 两个终端均先设置为本机 recipe 路径
RECIPE=/path/to/ling-cookbook/guide/finetuning/ling-3.0-tiny/undercover-player

# 1. 起模型服务（QLoRA adapter 挂载）
python3 serve.py --model ~/models/Ling-3.0-tiny-4bit \
    --adapter-path "$RECIPE/adapters-v2-run3" --host 127.0.0.1 --port 8080 --trust-remote-code

# 2. 起玩家 Bot（接入 BCS；--bcs-port 是 BCS 的 WS 端口，singlebox 默认 21000）
python3 main.py --bcs-port 21000 --base-url http://127.0.0.1:8080/v1 \
    --adapter-path "$RECIPE/adapters-v2-run3"
```

依赖：`pip install websockets openai`（独立 venv，勿与训练环境混用）。

## 测试

```bash
python3 test_loopback.py   # mock BCN 服务器回环：握手/ACK/final/inject/history/abort 六链路
```

## 调试沉淀（上真环境前踩掉的坑）

1. **同步推理阻塞事件循环** → 心跳超时、abort 处理不及时。`on_task` 里用
   `asyncio.to_thread` 包住推理调用。
2. **websockets ≥14 默认读环境代理**，连 `127.0.0.1` 的 BCS 也走 SOCKS 导致
   连接失败 → `websockets.connect(url, proxy=None)`。
3. **模型输出带 chat 模板残渣**（`</think>` 前缀、`<|role_end|>` 尾巴，Ling
   模板 thinking 块所致）→ 清洗层统一剥离；详见 recipe README Troubleshooting #6。
4. **清洗层 strip 顺序**：先剥序号再去 markdown 记号，否则 `1. **分析：**` 类
   前缀漏网；markdown 符号全量删除、尾部残缺标点不计入发言。


## 2026-11-14 运行契约更新

- `serve.py` 默认思考开启；`MLX_THINKING=0` 显式关闭，请求的 `chat_template_kwargs.enable_thinking` 可覆盖。
- 调用微调玩家必须显式传 `main.py --adapter-path`，并与服务启动配置一致；只有明确使用 `--base-model` 才允许不传。模型别名本身不选择 adapter。
- LoRA 开关只在串行生成线程切换；底座与微调缓存命名空间分离；不匹配的 adapter 路径被拒绝。
- Bot 请求用 temperature=0.7、top_p=0.9、seed=42，显式开启思考；禁用 SDK 隐式重试，单请求超时120秒，最多两种输出预算256/512。
- 规则层剥离思考正文，非法、自票及已知出局票转弃权；PK只选择已知合法非本人候选。若上游未给出合法候选，无法保证产生合法PK动作。
- BCN abort 已验证会丢弃迟到 final；不等于远程模型计算被立即取消。
- 已完成三局隔离真实 BCS 接入验收：一席真实 MLX v2 加五席脚本控制，均经官方裁判进入 FINISHED 并结束成功；摘要见 [verification/real-table-acceptance.json](verification/real-table-acceptance.json)。这不是多模型竞技或胜率基准。
- 真实牌桌不提供隐藏身份；当前入口默认平民提示是旧模型的兼容假设，不能声称等同离线有真实身份的评测条件。
- 前缀缓存收益必须由 cached_tokens 证实；旧“只改后缀”的冷请求测法不成立，不应直接引用其加速比。

影响范围：只修改此独立玩家的本地模型客户端、服务启动包装与输出规则；不改变BCN帧契约或Avernet核心实现。
迁移：旧启动命令补齐adapter路径；如果确实需要底座，添加--base-model。测试覆盖test_runtime.py与test_loopback.py。
