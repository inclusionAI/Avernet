"""Read the AgentBench Markdown template without BCN-specific Case fields."""
import math
import re
from pathlib import Path
import yaml


def load_case(path):
    text = Path(path).read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
    if not match:
        raise ValueError("Case requires YAML frontmatter")
    meta = yaml.safe_load(match[1])
    sections = dict(re.findall(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", match[2], re.M | re.S))
    if not isinstance(meta, dict) or not re.fullmatch(r"[A-Za-z0-9_-]+", str(meta.get("id", ""))):
        raise ValueError("Invalid case id")
    if not sections.get('Prompt', '').strip():
        raise ValueError('Missing section: Prompt')
    if 'bcn' in meta or 'llm_judge_dimensions' in meta:
        raise ValueError('Put team configuration in Profile and scoring dimensions in Rubric')
    kind = meta.setdefault('grading_type', 'automated')
    if kind not in ('automated', 'llm_judge', 'hybrid'):
        raise ValueError('Unsupported grading_type')
    if meta.get('workspace_files', []) != []:
        raise ValueError('Nonempty workspace_files is not supported for group material distribution yet')
    timeout = meta.setdefault('timeout_seconds', 120)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Invalid execution timeout')
    if kind == 'hybrid':
        weights = meta.setdefault('grading_weights', {'automated': .5, 'llm_judge': .5})
        if (not isinstance(weights, dict) or set(weights) != {'automated', 'llm_judge'}
                or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in weights.values())
                or not math.isfinite(sum(weights.values())) or sum(weights.values()) <= 0):
            raise ValueError('Hybrid weights must be finite, nonnegative and have positive total')
        total = sum(weights.values())
        meta['grading_weights'] = {key: value / total for key, value in weights.items()}
    if kind in ('llm_judge', 'hybrid'):
        rubric = sections.get('LLM Judge Rubric', '').strip() or sections.get('Grading Criteria', '').strip()
        if not rubric:
            raise ValueError('Model grading requires LLM Judge Rubric or Grading Criteria')
    code = re.search(r"```python\s*\n(.*?)```", sections.get('Automated Checks', ''), re.S)
    if kind in ('automated', 'hybrid') and not code:
        raise ValueError('Missing Python Automated Checks')
    return {'meta': meta, 'sections': sections, 'code': code[1] if code else ''}
