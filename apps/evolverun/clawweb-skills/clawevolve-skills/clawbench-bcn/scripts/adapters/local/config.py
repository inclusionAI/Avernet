"""Local composition configuration. Import only model fields from personal OpenClaw."""
import json
from pathlib import Path
import shutil
import subprocess


def load_local_config(model_path=None, output_dir=None):
    repo = next((p for p in Path(__file__).resolve().parents if (p / 'apps/bcs').is_dir()), None)
    if repo is None:
        raise ValueError('Local mode requires an Avernet checkout')
    path = Path(model_path or '~/.openclaw/openclaw.json').expanduser()
    model = json.loads(path.read_text())
    primary = model.get('agents', {}).get('defaults', {}).get('model')
    primary = primary.get('primary') if isinstance(primary, dict) else primary
    if not isinstance(primary, str) or '/' not in primary:
        raise ValueError('A primary model provider/model is required')
    provider_id, model_id = primary.split('/', 1)
    provider = model.get('models', {}).get('providers', {}).get(provider_id)
    if not isinstance(provider, dict):
        raise ValueError('Primary model provider configuration missing')
    openclaw = shutil.which('openclaw')
    if not openclaw:
        raise ValueError('openclaw is not in PATH')
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js >=22.19 is required')
    version = subprocess.check_output([node, '--version'], text=True).strip().lstrip('v')
    parts = tuple(int(x) for x in version.split('.')[:2])
    if parts < (22, 19):
        raise ValueError('Select Node.js >=22.19 in PATH before starting the Bench')
    plugin = repo / 'apps/bcs/crates/plugins/openclaw-channel-bcn'
    plugin = next((p for p in (plugin, plugin / 'package') if (p / 'dist/esm/index.js').is_file()), None)
    if plugin is None:
        raise ValueError('Build the BCN plugin before running the Bench')
    return {'repository': repo, 'output_dir': Path(output_dir or '~/.cache/clawbench-bcn/runs').expanduser().resolve(),
        'model_config_path': path.resolve(), 'model_data': model, 'provider': provider, 'model_id': model_id,
        'bcs_url': 'http://127.0.0.1:21000', 'openclaw': openclaw, 'plugin_path': plugin,
        'port_start': 32001, 'startup_timeout_seconds': 120,
        'judge': {'timeout_seconds': 120}}
