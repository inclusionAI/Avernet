"""Local backend readiness checks; these do not certify a real game table."""
import argparse
import json
from pathlib import Path
import time

import httpx
from openai import OpenAI

LONG_FILL = '谁是卧底游戏中玩家轮流用一句话描述自己的词语。' * 400
VOTE_PROBE = '请只输出「我投2号」或「我弃权」：'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', default='ling-3.0-tiny')
    parser.add_argument('--adapter-path')
    parser.add_argument('--max-seconds', type=float, default=120)
    parser.add_argument('--node-budget', type=float, default=420)
    parser.add_argument('--out')
    args = parser.parse_args()
    if args.max_seconds <= 0 or args.node_budget <= 0:
        parser.error('Time budgets must be positive')
    client = OpenAI(base_url=args.base_url, api_key='local', max_retries=0,
                    http_client=httpx.Client(trust_env=False, timeout=args.max_seconds))
    results = []
    for label, prompt, adapter, budget in [
            ('base-long', LONG_FILL, None, 16), ('base-short', VOTE_PROBE, None, 32),
            *([('adapter-short', VOTE_PROBE, args.adapter_path, 256)] if args.adapter_path else [])]:
        try:
            extra = {'chat_template_kwargs': {'enable_thinking': True}}
            if adapter:
                extra['adapters'] = adapter
            start = time.perf_counter()
            response = client.chat.completions.create(
                model=args.model, messages=[{'role': 'user', 'content': prompt}],
                max_tokens=budget, temperature=0.7, top_p=0.9, seed=42, extra_body=extra)
            elapsed = time.perf_counter() - start
            choice = response.choices[0]
            message = choice.message.model_dump()
            content = (message.get('content') or '').strip()
            reasoning = message.get('reasoning') or message.get('reasoning_content') or ''
            # A truncated base thinking response proves inference works, not answer readiness.
            passed = bool(content or reasoning)
            if adapter:
                passed = bool(content) and choice.finish_reason != 'length'
            if label == 'base-long':
                passed = passed and response.usage.prompt_tokens > 2048
            row = {'label': label, 'passed': passed, 'seconds': elapsed,
                   'prompt_tokens': response.usage.prompt_tokens,
                   'finish_reason': choice.finish_reason, 'content_chars': len(content),
                   'reasoning_present': bool(reasoning),
                   'within_node_quarter': elapsed <= args.node_budget / 4}
        except Exception as error:
            row = {'label': label, 'passed': False, 'error': type(error).__name__}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {'passed': all(row['passed'] for row in results), 'results': results,
              'scope': 'Backend only. Base reasoning is not a playable answer; no real BCS/game validation.'}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
