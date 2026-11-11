"""Small standalone reader for the existing ClawBench Markdown contract."""
import math
import re
from pathlib import Path
import yaml

MEMBERS = {"ceo", "product-manager", "engineering", "verification", "customer-service"}

def load_case(path):
    text = Path(path).read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
    if not match:
        raise ValueError("Case requires YAML frontmatter")
    meta = yaml.safe_load(match[1])
    sections = dict(re.findall(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", match[2], re.M | re.S))
    for name in ("Prompt", "Expected Behavior", "Automated Checks", "LLM Judge Rubric"):
        if not sections.get(name, "").strip():
            raise ValueError(f"Missing section: {name}")
    if not isinstance(meta, dict) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(meta.get("id", ""))):
        raise ValueError("Invalid case id")
    if meta.get("grading_type") != "hybrid" or meta.get("workspace_files", []) != []:
        raise ValueError("P1 supports hybrid, without workspace materials")
    if meta.get("bcn", {}).get("entry_member") != "ceo":
        raise ValueError("P1 entry_member must be ceo")
    timeout = meta.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Invalid execution timeout")
    weights = meta.get("grading_weights", {})
    if set(weights) != {"automated", "llm_judge"} or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in weights.values()) or not math.isclose(sum(weights.values()), 1):
        raise ValueError("Weights must be finite, nonnegative and sum to one")
    code = re.search(r"```python\s*\n(.*?)```", sections["Automated Checks"], re.S)
    if not code:
        raise ValueError("Missing Python Automated Checks")
    return {"meta": meta, "sections": sections, "code": code[1]}
