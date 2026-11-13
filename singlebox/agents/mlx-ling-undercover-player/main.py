"""「谁是卧底」MLX 玩家 Bot 入口。

启动本地模型服务后运行本进程接入牌桌：
    python3 -m mlx_lm.server --model ~/models/Ling-3.0-tiny-4bit \
        --adapter-path adapters/ --host 127.0.0.1 --port 8080 --trust-remote-code

    python3 main.py --bcs-port 8081 --base-url http://127.0.0.1:8080/v1

环境变量 BCS_PORT / BASE_URL 可替代命令行参数。
"""

import argparse
import asyncio
import logging
import os

from bcn_client import BcnClient
from player import GamePlayer

log = logging.getLogger("mlx-player")

TOKEN_FILE = os.path.expanduser("~/.mlx-ling-player.token")

BOT_NAME = "玩家MLX小林"
BOT_SUMMARY = ("「谁是卧底」玩家，Apple MLX 本地端侧引擎（Ling-3.0-tiny + QLoRA，M4 端侧）；"
               "发言 ≤25 字不泄词，投票输出「我投N号」/「我弃权」。")
BOT_SKILLS = "one-line-word-description,format-safe-voting,engine-mlx-local"
BOT_DOMAINS = "undercover-game,social-deduction,edge-inference"


def make_infer_fn(base_url, model_id="ling-3.0-tiny", adapter_path=None):
    """OpenAI 兼容推理后端（mlx_lm.server / vLLM 均可）。

    adapter_path 非空时随请求带 `"adapters"` 字段，命中 serve.py 的请求级
    LoRA 开关（单实例底座/微调双角色）；为空则走底座档。
    """
    import httpx
    from openai import OpenAI
    # httpx 默认读 HTTP_PROXY/ALL_PROXY 环境变量——连 127.0.0.1 的模型服务也会被
    # 送进代理（实测卡死在 7892 端口的代理连接上，事件循环空转等回包）。本地回环
    # 永远不该走代理：trust_env=False + 显式超时。（与 bcn_client 的 proxy=None
    # 同一类坑，见 README 调试沉淀 #2。）
    client = OpenAI(base_url=base_url, api_key="EMPTY",
                    http_client=httpx.Client(trust_env=False, timeout=300.0))
    extra = {"adapters": adapter_path} if adapter_path else {}

    def infer(prompt):
        # Ling-3.0-tiny 是推理模型：max_tokens 太小时 reasoning 吃掉预算、content 为
        # null。预算给足；若仍无正文（被截断），回退「过」保证不空发言。
        # 推理异常（超时/服务忙）同样回退而不是抛出：节点 420s 预算下沉默=双倍
        # 超时，回退句经清洗层仍满足输出契约（投票兜底为弃权），裁判永远可解析。
        for budget in (256, 512):
            try:
                resp = client.chat.completions.create(
                    model=model_id,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,  # 贪心：格式稳定，保证 100% 可解析
                    max_tokens=budget,
                    extra_body=extra or None,
                )
            except Exception:
                log.exception("model call failed (budget=%d) -> fallback", budget)
                return "过"
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        return "过"

    return infer


def make_onboard_fn(bcs_cli):
    """握手成功后用 bcs-cli onboard 注册名字与能力（token 走握手下发的 env）。"""

    async def onboard(client):
        proc = await asyncio.create_subprocess_exec(
            bcs_cli, "onboard",
            "--name", BOT_NAME,
            "--summary", BOT_SUMMARY,
            "--skills", BOT_SKILLS,
            "--domains", BOT_DOMAINS,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        log.info("onboard rc=%s: %s", proc.returncode, out.decode()[:300])
        # 持久化 token：重连恢复身份（BCN V3 要求）
        if client.token:
            with open(TOKEN_FILE, "w") as f:
                f.write(client.token)

    return onboard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bcs-port", default=os.environ.get("BCS_PORT", "8081"))
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8080/v1"))
    ap.add_argument("--bcs-cli", default=os.environ.get("BCS_CLI", "bcs-cli"),
                    help="bcs-cli 可执行文件路径（onboard 用）")
    ap.add_argument("--adapter-path", default=os.environ.get("ADAPTER_PATH"),
                    help="QLoRA adapter 目录；非空时随请求带 adapters 字段走微调档")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # 身份恒按「平民」提示：裁判发牌时随机翻转词对的平民/卧底列，玩家只知道自己
    # 的词，从词推身份在原理上不可行（真实游戏中玩家本就不知道身份）。身份只影响
    # 提示词里的自称，发言/投票格式由 rules 层兜底，不受影响。
    player = GamePlayer(make_infer_fn(args.base_url, adapter_path=args.adapter_path),
                        undercover_words=set())
    client = BcnClient(f"ws://127.0.0.1:{args.bcs_port}/ws/bot", on_task=player.on_task,
                       on_connected=make_onboard_fn(args.bcs_cli))
    if os.path.exists(TOKEN_FILE):
        client.token = open(TOKEN_FILE).read().strip() or None
        if client.token:
            log.info("restored token from %s", TOKEN_FILE)
    log.info("starting MLX undercover player -> %s", args.bcs_port)
    asyncio.run(client.run_forever())


if __name__ == "__main__":
    main()
