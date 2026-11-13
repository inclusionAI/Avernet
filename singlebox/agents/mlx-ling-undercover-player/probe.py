"""服务器探针：短问答 + referee 尺寸长 prompt，验证 Metal 不 OOM。

用法: python3 probe.py <port> [short|long]
"""

import json
import sys
import time
import urllib.request

port = int(sys.argv[1])
mode = sys.argv[2] if len(sys.argv) > 2 else "short"

if mode == "short":
    user = "请用一句话介绍谁是卧底游戏。"
else:
    # ~7800 token 的中文长文，模拟裁判 prompt（SKILL + GroupContext + 对局历史）
    para = (
        "谁是卧底是一款推理游戏：每局若干玩家各拿一个词，多数人词相同（平民），"
        "少数人词不同（卧底）。每轮每人用一句话描述自己的词但不能说出词本身，"
        "随后投票放逐一人，若卧底全部出局则平民胜，若卧底存活至最后一轮则卧底胜。"
    )
    user = (para * 60) + "\n请判断当前局势并给出下一步指令。"

req = urllib.request.Request(
    f"http://127.0.0.1:{port}/v1/chat/completions",
    data=json.dumps(
        {
            "model": "ling-3.0-tiny",
            "messages": [{"role": "user", "content": user}],
            "max_tokens": 64,
            "temperature": 0.7,
        }
    ).encode(),
    headers={"Content-Type": "application/json"},
)

t0 = time.time()
d = json.load(urllib.request.urlopen(req, timeout=600))
m = d["choices"][0]["message"]
print(f"--- port {port} {mode} ({time.time() - t0:.1f}s): {repr((m.get('content') or str(d))[:120])}")
