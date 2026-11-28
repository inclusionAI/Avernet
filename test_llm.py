"""One-off connectivity probe for DirectModelJudge against a live gateway."""

import asyncio
import json
import os
import sys
from pathlib import Path

# 运行前在当前终端设置环境变量，不要把真实密钥写入脚本或提交到 Git：
# export JUDGE_API_KEY='<your-api-key>'
# export JUDGE_BASE_URL='https://<your-model-endpoint>/v1'
# 可选：export JUDGE_MODEL='<model-name>'


async def main() -> None:
    api_key = os.environ.get("JUDGE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("请先设置环境变量 JUDGE_API_KEY；不要将 API Key 写入脚本。")
    base_url = os.environ.get("JUDGE_BASE_URL", "").strip()
    if not base_url:
        raise SystemExit("请先设置环境变量 JUDGE_BASE_URL。")

    sys.path.insert(
        0, str(Path(__file__).resolve().parent / "apps/evolution_prototype/src")
    )
    import httpx

    from bot_verification.plugins.direct_model_judge import DirectModelJudge

    model = os.environ.get("JUDGE_MODEL", "DeepSeek-V4.1-Flash")
    judge = DirectModelJudge(
        base_url=base_url,
        api_key=api_key,
        models={"score": model, "critic": model, "arbitrator": model},
        timeout_s=120.0,
        max_tokens=512,
        client_factory=lambda: httpx.AsyncClient(
            trust_env=False, follow_redirects=False
        ),
    )
    print("adapter version:", judge.version)
    payload = {
        "instructions": "you are a helpful assistant",
        "stage_instructions": "你是谁?",
    }
    result = await judge.invoke("score", payload)
    print("INVOKE SUCCESS")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
