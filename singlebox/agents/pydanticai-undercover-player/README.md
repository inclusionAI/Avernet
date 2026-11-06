# PydanticAI 谁是卧底玩家

通过 BCN V3 将一名 PydanticAI 玩家加入现有“谁是卧底”牌桌。
PydanticAI 负责模型执行、结构化输出和规则校验重试，主持人和 BCS 服务端保持原样。

## 快速启动

前置条件：已有正常运行的 Avernet 本地牌桌、`uv` 和 DeepSeek API Key。
尚未准备牌桌时，先完成[对局教程](../../../docs/undercover-game-tutorial.zh-CN.md)。

在仓库根目录执行：

```bash
cd singlebox/agents/pydanticai-undercover-player
uv sync --frozen --python 3.12
test -f .env || cp .env.example .env
```

编辑 `.env` 填写 API Key，核对 BCS 地址，然后启动：

```bash
uv run --frozen --env-file .env pydanticai-player
```

出现 `PydanticAI player connected; engine ready` 后，在新建的任务协作群中选择
原主持人作为 manager，四名 OpenClaw 玩家和本玩家作为 worker。人类加入当前会话后发送 `开始`。

完整配置、组局、成功标志和排障步骤见 **[接入笔记](INTEGRATION-NOTE.zh-CN.md)**。
同一进程内重连保留本人信息，进程重启后该内存不恢复；对局期间保持玩家进程运行。

## 测试

```bash
uv run --frozen pytest -q
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
```

测试不调用外部模型；WebSocket 测试需允许监听本地回环端口。
覆盖原主持人规则/票面解析、Agent 校验重试、取消与迟到处理、重连和会话隔离。

## 三局详细日志

- [第 1 局](validation/game-01.zh-CN.md)
- [第 2 局](validation/game-02.zh-CN.md)
- [第 3 局](validation/game-03.zh-CN.md)

日志包含公开发言、票面、协作节点时间线、计票、违规统计及异常，记录各自的运行条件和版本。
