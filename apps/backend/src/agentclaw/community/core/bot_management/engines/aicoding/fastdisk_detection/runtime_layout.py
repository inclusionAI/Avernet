"""Adapt the existing persistence gate without changing its run/skip decisions."""
import json
import pwd
import stat
from pathlib import Path


def runtime_layout():
    config_path = Path('/var/run/agentclaw/persist-runtime.json')
    config = {}
    if config_path.exists():
        st = config_path.lstat()
        # ARCA may execute as root while startup writes the config as admin.
        # Trust the known owners, not whichever user happens to run detection.
        trusted_uids = {0, pwd.getpwnam('admin').pw_uid}
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in trusted_uids or st.st_mode & 0o022:
            raise ValueError('unsafe persistence runtime configuration')
        config = json.loads(config_path.read_text())
    home = config.get('AIX_FASTDISK_HOME_BASE', '/home/admin/.aicoding')
    storage = config.get('AICODING_ROOT', '/opt/.aicoding')
    for value in (home, storage):
        if not isinstance(value, str) or not Path(value).is_absolute() or '..' in Path(value).parts:
            raise ValueError('invalid runtime path')
    return {'home': home, 'storage': storage}
