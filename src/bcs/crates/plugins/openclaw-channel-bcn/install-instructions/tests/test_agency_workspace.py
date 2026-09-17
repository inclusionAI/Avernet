"""Filesystem-only tests for profile overwrite behavior."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from agency_launcher import prepare_workspace  # noqa: E402
from agency_profiles import parse_profile_document  # noqa: E402


class WorkspaceOverwriteTest(unittest.TestCase):
    def test_overwrite_backs_up_the_previous_profile_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / 'instance'
            args = SimpleNamespace(
                agency_dir=Path(temporary),
                bcn_plugin='@avernet-plugin/openclaw-channel-bcn@1.0.23',
            )
            model = {'model': {'primary': 'demo/test'}, 'models': {}}
            old_source = '---\nname: Backend\ndescription: Old role\n---\nOld body\n'
            new_source = '---\nname: Backend\ndescription: New role\n---\nNew body\n'
            old_profile = parse_profile_document(old_source, 'engineering/backend.md')
            new_profile = parse_profile_document(new_source, 'engineering/backend.md')

            prepare_workspace(old_profile, state, 19000, args, model,
                              'ws://127.0.0.1:21000/ws/bot')
            prepare_workspace(new_profile, state, 19000, args, model,
                              'ws://127.0.0.1:21000/ws/bot', overwrite_profile=True)

            backups = list(state.glob('profile.previous.*.md'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), old_source)
            self.assertEqual((state / 'profile.md').read_text(), new_source)


if __name__ == '__main__':
    unittest.main()
