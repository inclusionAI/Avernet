"""File output and evaluator input loading; no task execution or scoring."""
import json
import time
import uuid
from pathlib import Path


def write_json(path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def summary(result, report=False):
    link = '[Evaluation report](analysis-report.md)\n\n' if report else 'Report: awaiting independent evaluation Agent.\n\n'
    return '# BCN Bench result\n\n' + link + '```json\n' + json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n```\n\nEvidence: evidence/; final: artifacts/final.md. Private runtime configuration/logs must not be uploaded.\n'


class FileOutput:
    def __init__(self, root):
        self.run_id = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:10]
        self.directory = Path(root).expanduser().resolve() / self.run_id
        self.directory.mkdir(parents=True, mode=0o700)
        self.artifacts = self.directory / 'artifacts'
        self.evidence = self.directory / 'evidence'
        self.artifacts.mkdir(); self.evidence.mkdir()

    def progress(self, message):
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        with (self.directory / 'runner.log').open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')

    def write_json(self, name, data):
        write_json(self.directory / name, data)

    def write_text(self, name, text):
        write_text(self.directory / name, text)

    def save_result(self, info, result):
        self.write_json('run.json', info)
        self.write_json('result.json', result)
        self.write_text('report.md', summary(result))


def load_evaluation(directory, report_path, assessment_path=None):
    directory = Path(directory).expanduser().resolve()
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    return (read(directory / 'evaluation-request.json'), read(directory / 'result.json'),
            Path(report_path).read_text(encoding='utf-8'), read(assessment_path) if assessment_path else {})


def save_evaluation(directory, result, report):
    directory = Path(directory).expanduser().resolve()
    # Serialize before any write, so invalid model values cannot partially update files.
    content = summary(result, report=True)
    write_text(directory / 'analysis-report.md', report)
    write_text(directory / 'report.md', content)
    write_json(directory / 'result.json', result)
