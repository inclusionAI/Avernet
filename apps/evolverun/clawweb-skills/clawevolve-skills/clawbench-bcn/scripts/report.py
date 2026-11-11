"""Generate a single-run report from archived evidence; never rerun Bots or change scores."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import ssl
import urllib.request
from pathlib import Path
from grading_adapter import NoRedirect
from runtime import load_runtime


def generate_report(directory, config, prompt_path=None):
    directory = Path(directory).resolve()
    prompt = Path(prompt_path) if prompt_path else Path(__file__).resolve().parents[1] / "prompts/report.md"
    evidence = {}
    budget = 100000
    truncated = []
    files = [directory / "run.json", directory / "result.json", directory / "artifacts/final.md",
             directory / "evidence/group-messages.json", *sorted((directory / "evidence").glob("*/*.jsonl"))]
    for file in files:
        if not file.is_file():
            continue
        name = str(file.relative_to(directory))
        text = file.read_text()
        # Bound every source so one large transcript cannot consume the whole request.
        limit = min(18000, max(0, budget))
        if len(text) > limit:
            truncated.append(name)
            text = text[:limit // 2] + "\n[...截断...]\n" + text[-limit // 2:] if limit else "[未纳入，预算已满]"
        evidence[name] = text
        budget -= min(limit, len(text))
    timeline = []
    group_path = directory / "evidence/group-messages.json"
    if group_path.is_file():
        for message in sorted(json.loads(group_path.read_text()), key=lambda m: m.get("timestamp", 0)):
            stamp = message.get("timestamp")
            if not isinstance(stamp, (int, float)):
                continue
            timeline.append({"time_Asia_Shanghai": datetime.fromtimestamp(stamp / 1000, ZoneInfo("Asia/Shanghai")).isoformat(),
                             "sender": message.get("sender"), "role": message.get("role"),
                             "tool": message.get("metadata", {}).get("tool_name"),
                             "excerpt": str(message.get("content", ""))[:240]})
    body = {"evidence": evidence, "truncated_sources": truncated, "group_timeline": timeline,
            "verified_runner_contract": "终态取BCS Session.status=completed且output非空、无error_message。Manager完成工具名为bcs_task_complete。不按关键词、静默或口头声明结束。超时跳过质量评分。报告不得改分。所有展示时间采用Asia/Shanghai UTC+8。"}
    judge = config["judge"]
    payload = {"model": judge["model"], "messages": [{"role": "system", "content": prompt.read_text()},
                {"role": "user", "content": json.dumps(body, ensure_ascii=False)}]}
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        context = ssl.create_default_context()
    else:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(judge["url"], data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ[judge["api_key_env"]]})
    metadata = {"model": judge["model"], "truncated_sources": truncated}
    try:
        with urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context)).open(request, timeout=judge["timeout_seconds"]) as response:
            answer = json.load(response)
        text = answer["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Empty report")
        output = directory / "analysis-report.md"
        temp = directory / "analysis-report.md.tmp"
        temp.write_text(text)
        temp.replace(output)
        metadata.update(status="completed", usage=answer.get("usage"))
    except Exception as error:
        metadata.update(status="error", error_type=type(error).__name__)
        raise
    finally:
        (directory / "report-generation.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--runtime")
    parser.add_argument("--prompt", help="Optional external report prompt")
    args = parser.parse_args()
    print(generate_report(args.run_dir, load_runtime(args.runtime), args.prompt))
