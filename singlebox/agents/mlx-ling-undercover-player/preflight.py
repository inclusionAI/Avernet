"""开局前模型后端探针：长提示词冒烟 + 底座/LoRA 判别 + 时延预算检查。

为什么需要它（三个真实事故）：
1. `/v1/models` 在模型加载完成前就返回 200——「端口活着」≠「模型能推理」；
2. mlx-lm 的量化 KV bug 只在 >2048 token 提示词上炸（短冒烟永远测不出），
   表现为玩家 Bot 全员 empty response；
3. 内存挤压把推理拖慢 10 倍，节点 420s 预算静默超时——要在开局前测出来。

用法：
    python preflight.py --base-url http://127.0.0.1:8081/v1 \
        [--model ling-3.0-tiny] [--adapter-path <adapter目录>] \
        [--max-seconds 120] [--node-budget 420]

退出码：0 = 全绿可开局；1 = 有硬故障（打印原因）。
LoRA 判别只作提示（贪心解码偶尔同解），硬故障以长提示词冒烟为准。
"""

import argparse
import sys
import time

import httpx
from openai import OpenAI

# 超过 2048 token 才能踩到量化 KV 切换点；~1.7 个汉字 ≈ 1 token（保守按 2）
LONG_FILL = "谁是卧底游戏中玩家轮流用一句话描述自己的词语。" * 400  # ≈2600+ token
VOTE_PROBE = ("你是「谁是卧底」的3号玩家，词语是【饺子】。第2轮投票："
              "1号说\"圆滚滚的甜食\"2号说\"煮着吃的\"3号说\"我爱吃馅的\"4号说\"有皮有馅\""
              "5号说\"白白的\"。请投票：只输出「我投N号」或「我弃权」，不要输出其他内容。")


def call(client, model, prompt, adapters=None, max_tokens=32):
    t0 = time.time()
    extra = {"adapters": adapters} if adapters else {}
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0, max_tokens=max_tokens,
        extra_body=extra or None,
    )
    text = (resp.choices[0].message.content or "").strip()
    return text, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", default="ling-3.0-tiny")
    ap.add_argument("--adapter-path", default=None,
                    help="QLoRA adapter 目录；给则额外测微调档并做双档判别")
    ap.add_argument("--max-seconds", type=float, default=120.0,
                    help="单次补全的时延红线（默认 120s）")
    ap.add_argument("--node-budget", type=float, default=420.0,
                    help="牌桌节点超时预算，用于给出裕量提示")
    args = ap.parse_args()

    # trust_env=False：本机回环不走代理（见 INTEGRATION.md 常见坑 #1 的同族坑）
    client = OpenAI(base_url=args.base_url, api_key="EMPTY",
                    http_client=httpx.Client(trust_env=False,
                                             timeout=args.max_seconds))
    hard_fail = False

    def probe(label, prompt, adapters=None, max_tokens=32):
        nonlocal hard_fail
        try:
            text, dt = call(client, args.model, prompt, adapters, max_tokens)
        except Exception as e:
            print(f"[FAIL] {label}: {type(e).__name__}: {e}")
            hard_fail = True
            return None
        if not text:
            print(f"[FAIL] {label}: 空回复（长提示词+空回复多半是量化 KV 崩溃或思考"
                  f"吃光预算，看服务端日志找 swapaxes/Traceback） dt={dt:.1f}s")
            hard_fail = True
            return None
        verdict = "OK" if dt <= args.node_budget / 4 else "SLOW"
        if verdict == "SLOW":
            print(f"[WARN] {label}: {dt:.1f}s 超过节点预算的 1/4（{args.node_budget}s），"
                  f"并发或挤压下可能超时")
        else:
            print(f"[ OK ] {label}: {dt:.1f}s ({len(text)} chars) {text[:40]!r}")
        return text

    print(f"== 长提示词冒烟（≈2600 token，踩量化 KV 切换点）-> {args.base_url}")
    probe("base 长提示", LONG_FILL, None, max_tokens=32)

    print("== 双档判别（投票提示词，底座/LoRA 在此强分歧；同解仅作提示）")
    t_base = probe("base 投票", VOTE_PROBE)
    if args.adapter_path:
        t_lora = probe("lora 投票", VOTE_PROBE, adapters=args.adapter_path)
        if t_base and t_lora:
            same = "（同解——不一定是故障，换提示词复测）" if t_base == t_lora else "（分歧✓ 开关生效）"
            print(f"       base={t_base!r} lora={t_lora!r} {same}")

    print("== 时延预算")
    print(f"       红线 {args.max_seconds}s / 节点预算 {args.node_budget}s")
    print(("== 结论：有硬故障，别开局" if hard_fail else "== 结论：可以开局"))
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
