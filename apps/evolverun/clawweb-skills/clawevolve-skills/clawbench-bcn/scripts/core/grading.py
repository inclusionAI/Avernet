"""Minimal hybrid evaluator. The LLM never computes the aggregate score."""
import json
import math
import subprocess
import sys
from pathlib import Path

def checked_scores(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("Scores must be a nonempty object")
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in value.values()):
        raise ValueError("Scores must be finite numbers in [0,1]")
    return value

def code_grade(case, artifacts, timeout, transcript=None):
    # Trusted code only; a subprocess timeout is not a security sandbox.
    if transcript is not None and not isinstance(transcript, list):
        raise ValueError('Judge transcript must be a message list')
    env = {}  # Do not pass parent credentials into the Judge.
    worker = Path(__file__).with_name("judge_worker.py")
    response = subprocess.run([sys.executable, "-I", str(worker)], input=json.dumps({"code": case["code"], "workspace": str(artifacts), "transcript": transcript if transcript is not None else []}),
        text=True, capture_output=True, timeout=timeout, env=env, cwd=artifacts)
    if response.returncode:
        raise ValueError("Automated Judge failed")
    return checked_scores(json.loads(response.stdout))

def combine(auto, llm_total, weights):
    a = checked_scores(auto)
    return sum(a.values()) / len(a) * weights['automated'] + llm_total * weights['llm_judge']
