from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BCS_MODULE = ROOT / 'singlebox/modules/bcs.sh'


class BcsPanelAssetTest(unittest.TestCase):
    def test_panel_build_reuses_fresh_output_and_rebuilds_stale_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tmp_path = Path(temporary)
            panel = tmp_path / 'apps/bcs/assets/panel'
            (panel / 'src').mkdir(parents=True)
            (panel / 'node_modules').mkdir()
            (panel / 'package.json').write_text('{"scripts":{"build":"vite build"}}\n')
            (panel / 'package-lock.json').write_text('{}\n')
            (panel / 'src/index.ts').write_text('export const panel = true;\n')
            (panel / 'node_modules/.package-lock.json').write_text('{}\n')
            (panel / 'node_modules/prune-probe.js').write_text('module.exports = 1;\n')
            (panel / 'dist').mkdir()
            (panel / 'dist/stale-chunk.js').write_text('export const stale = 1;\n')
            for path in (panel / 'package.json', panel / 'package-lock.json', panel / 'src/index.ts'):
                subprocess.run(['touch', '-t', '202001010000', str(path)], check=True)

            bin_dir = tmp_path / 'bin'
            bin_dir.mkdir()
            calls = tmp_path / 'npm-calls'
            npm = bin_dir / 'npm'
            npm.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$CALL_LOG\"\n"
                "if [[ \"$1 $2\" == 'run build' ]]; then\n"
                "  mkdir -p dist\n"
                "  printf 'panel-output\\n' > dist/index.umd.js\n"
                "fi\n"
            )
            npm.chmod(0o755)

            script = f'''
set -euo pipefail
BCS_DIR={tmp_path}/apps/bcs
LOG_DIR={tmp_path}/logs
DEP_DIR={tmp_path}/deps
REQUIRED_NODE_MAJOR=22
NPM_REGISTRY_URL=https://registry.npmjs.org
check_node_available() {{ return 0; }}
check_command() {{ return 0; }}
log_info() {{ :; }}
log_error() {{ :; }}
log_warn() {{ :; }}
source {BCS_MODULE}
build_bcs_panel_asset
touch -t 203001010000 "$BCS_PANEL_ASSET_DIR/dist/index.umd.js"
touch -t 203006010000 "$BCS_PANEL_ASSET_DIR/node_modules/prune-probe.js" "$BCS_PANEL_ASSET_DIR/dist/stale-chunk.js"
build_bcs_panel_asset
touch -t 203101010000 "$BCS_PANEL_ASSET_DIR/src/index.ts"
build_bcs_panel_asset
'''
            result = subprocess.run(
                ['bash', '-c', script],
                env={**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'CALL_LOG': str(calls)},
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            build_calls = [line for line in calls.read_text().splitlines() if line == 'run build']
            self.assertEqual(len(build_calls), 2, build_calls)


if __name__ == '__main__':
    unittest.main()
