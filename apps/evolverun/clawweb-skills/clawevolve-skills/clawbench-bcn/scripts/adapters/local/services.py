"""Prepare only loopback services; keep reused instances and all runtime data."""
import json
from pathlib import Path
import urllib.request
from .processes import listening


def healthy(url, kind):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=2) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            return False
        # Match the expected health response, not just any HTTP 200 listener.
        status = str(data.get('status', '')).lower()
        return status in ('ok', 'healthy', 'pass') or (kind == 'backend' and data.get('success') is True)
    except (OSError, ValueError):
        return False


class LocalServices:
    """Check user-managed services without changing their lifecycle."""

    def __init__(self, config, directory):
        self.config, self.directory = config, Path(directory)
        self.records = []

    def specs(self):
        return [('backend', 8888, '/api/health'),
                ('bcsfuse', 8765, '/health'), ('bcs', 21000, '/health')]

    def start(self, progress):
        missing, unhealthy = [], []
        for name, port, path in self.specs():
            state = 'missing'
            if listening(port):
                state = 'healthy' if healthy(f'http://127.0.0.1:{port}{path}', name) else 'unhealthy'
            self.records.append({'service': name, 'owned': False, 'port': port, 'status': state})
            if state == 'missing':
                missing.append(name)
            elif state == 'unhealthy':
                unhealthy.append(name)
            else:
                progress(f'复用健康服务: {name}')
        (self.directory / 'services.json').write_text(json.dumps(self.records, indent=2))
        if missing or unhealthy:
            lines = ['基础服务未就绪；Bench 不会启动或停止基础服务，也不会启动评测 Agent。']
            if missing:
                lines.append('未启动: ' + ', '.join(missing))
                lines.extend([
                    '请在 Avernet 仓库根目录执行（首次准备才需 setup；已准备好只需 start）：',
                    './singlebox/singlebox.sh setup backend bcsfuse bcs',
                    './singlebox/singlebox.sh start backend bcsfuse bcs',
                    '只指定以上基础服务，不使用无参数命令或 all，避免启动默认 Demo Agent。'])
            if unhealthy:
                lines.append('端口已占用但健康检查失败，请先排查，不会重启或杀进程: ' + ', '.join(unhealthy))
            for line in lines:
                progress(line)
            raise RuntimeError('\n'.join(lines))

    def stop(self):
        # All shared services belong to the user, including on failed runs.
        pass
