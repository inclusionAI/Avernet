"""Local prefix-cache probe. Cold requests differ before the long static prefix."""
import argparse
import json
import random
import statistics
import string
import time
from pathlib import Path

import httpx
from openai import OpenAI

FILL = "谁是卧底游戏中玩家轮流用一句话描述自己的词语。"
USER = "请只输出你的发言内容（不超过25个字）："


def timed_call(client, model, system, user):
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=model, messages=[{"role": "system", "content": system},
                               {"role": "user", "content": user}],
        temperature=0.0, seed=42, max_tokens=8,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
    )
    usage = response.usage.model_dump()
    return {"seconds": time.perf_counter() - start,
            "prompt_tokens": usage['prompt_tokens'],
            "cached_tokens": (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0),
            "completion_tokens": usage['completion_tokens'],
            "finish_reason": response.choices[0].finish_reason}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8081/v1')
    parser.add_argument('--model', default='ling-3.0-tiny')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--fill-repeats', type=int, default=160)
    parser.add_argument('--out', required=True)
    parser.add_argument('--require-cache-hit', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1 or args.fill_repeats < 1:
        parser.error('repeat counts must be positive')
    client = OpenAI(base_url=args.base_url, api_key='local', max_retries=0,
                    http_client=httpx.Client(trust_env=False, timeout=300))
    results = {name: [] for name in ['first', 'warm_hit', 'shared_tail', 'cold_distinct']}
    rng = random.Random(2026)
    for index in range(args.repeats):
        # Variation is before FILL; changing only its suffix is not a cold test.
        system = f'缓存测量轮次{index}：\n' + FILL * args.fill_repeats
        results['first'].append(timed_call(client, args.model, system, USER))
        results['warm_hit'].append(timed_call(client, args.model, system, USER))
        results['shared_tail'].append(timed_call(client, args.model, system, USER + f'第{index}桌'))
        nonce = ''.join(rng.choices(string.ascii_letters + string.digits, k=128))
        results['cold_distinct'].append(timed_call(client, args.model, nonce + '\n' + FILL * args.fill_repeats, USER))
        print('cache round', index + 1, '/', args.repeats, flush=True)
    means = {name: statistics.mean(row['seconds'] for row in rows) for name, rows in results.items()}
    fractions = {name: statistics.mean(row['cached_tokens'] / row['prompt_tokens'] for row in rows)
                 for name, rows in results.items()}
    checks = {'warm_cache_hit': fractions['warm_hit'] >= 0.8,
              'cold_prefix_is_distinct': fractions['cold_distinct'] < 0.1}
    report = {'mean_seconds': means, 'mean_cached_fraction': fractions, 'checks': checks,
              'warm_vs_cold_speedup': means['cold_distinct'] / means['warm_hit'],
              'shared_tail_vs_cold_speedup': means['cold_distinct'] / means['shared_tail'],
              'raw': results,
              'scope': 'Local serial prefill-oriented test with 8 generated tokens; not game latency or quality.'}
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'raw'}, indent=2))
    if not checks['cold_prefix_is_distinct']:
        raise SystemExit('Invalid cold comparison: excessive cached prefix')
    if args.require_cache_hit and not checks['warm_cache_hit']:
        raise SystemExit('No cache-hit benefit observed')


if __name__ == '__main__':
    main()
