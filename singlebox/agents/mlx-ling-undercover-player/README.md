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
| 安全层 | `rules.py` | 强制 25 字上限、泄词字删除、投票格式正则兜底、模板残渣剥离——**保证裁判 100% 可解析，模型跑偏也不违规** |

`main.py` 组装三层；模型侧走 OpenAI 兼容接口（`mlx_lm.server` 或 vLLM 均可），
换引擎只需换 `--base-url`。

## 运行

```bash
# 1. 起模型服务（QLoRA adapter 挂载）
python3 -m mlx_lm.server --model ~/models/Ling-3.0-tiny-4bit \
    --adapter-path adapters/ --host 127.0.0.1 --port 8080 --trust-remote-code

# 2. 起玩家 Bot（接入 BCS；--bcs-port 是 BCS 的 WS 端口，singlebox 默认 21000）
python3 main.py --bcs-port 21000 --base-url http://127.0.0.1:8080/v1
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
3. **模型输出带 chat 模板残渣**（`</thinking>` 前缀、`<|role_end|>` 尾巴，Ling
   模板 thinking 块所致）→ 清洗层统一剥离；详见 recipe README Troubleshooting #6。
4. **清洗层 strip 顺序**：先剥序号再去 markdown 记号，否则 `1. **分析：**` 类
   前缀漏网；markdown 符号全量删除、尾部残缺标点不计入发言。
