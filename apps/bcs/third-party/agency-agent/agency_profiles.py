"""Read role data and compose isolated OpenClaw configuration (no execution)."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

GATEWAY_AUTH_FIELD = 'token'


@dataclass(frozen=True)
class Profile:
    path: str
    name: str
    description: str
    emoji: str
    body: str
    source: str
    digest: str

    @property
    def instance_id(self) -> str:
        stem = re.sub(r'[^a-z0-9_-]', '-', Path(self.path).stem.lower()).strip('-') or 'agent'
        suffix = hashlib.sha256(self.path.encode()).hexdigest()[:10]
        return f'{stem[:60]}-{suffix}'


def parse_profile_document(source: str, relative_path: str, selector: str = 'profile') -> Profile:
    lines = source.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        raise ValueError(f'{selector}: missing YAML frontmatter')
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == '---'), None)
    if end is None:
        raise ValueError(f'{selector}: unterminated YAML frontmatter')
    try:
        metadata = yaml.safe_load(''.join(lines[1:end]))
    except yaml.YAMLError:
        raise ValueError(f'{selector}: invalid YAML frontmatter') from None
    if not isinstance(metadata, dict):
        raise TypeError(f'{selector}: frontmatter must be an object')
    for key in ('name', 'description'):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{selector}: {key} must be nonempty text')
    name = metadata['name'].strip()
    if not 2 <= len(name) <= 64 or any(ord(char) < 32 for char in name):
        raise ValueError(f'{selector}: name must be 2–64 characters without control characters')
    body = ''.join(lines[end + 1:]).strip()
    if not body:
        raise ValueError(f'{selector}: profile body is empty')
    emoji = metadata.get('emoji', '')
    if not isinstance(emoji, str):
        raise TypeError(f'{selector}: emoji must be text')
    return Profile(relative_path, name, metadata['description'].strip(), emoji,
                   body + '\n', source, hashlib.sha256(source.encode()).hexdigest())


def _selection_parts(value: str, *, team_only: bool = False) -> list[str]:
    parts = value.split('/')
    if (not value or (team_only and len(parts) != 1)
            or (not team_only and len(parts) < 2)
            or any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', part) for part in parts)):
        spelling = 'team' if team_only else 'team/profile'
        raise ValueError(f'selection must use {spelling} format without absolute paths or traversal')
    return parts


def _contained_file(repo: Path, relative: Path) -> Path:
    try:
        resolved = (repo / relative).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError(f'profile not found: {relative.as_posix()}') from None
    if not resolved.is_relative_to(repo) or not resolved.is_file():
        raise ValueError('profile must be a Markdown file contained in --agency-dir')
    return resolved


def load_profiles(repo: Path, selections: list[tuple[str, str]]) -> list[Profile]:
    """Expand ordered profile/team selections; canonical file paths are identities.

    `team/profile` and `team/profile.md` refer to the same file. Team members are
    sorted by repository-relative path (including nested divisions); repeated
    selections keep their first position, never create duplicate instances.
    """
    repo = repo.resolve(strict=True)
    profiles: list[Profile] = []
    seen: set[Path] = set()
    for kind, selector in selections:
        if kind == 'team':
            _selection_parts(selector, team_only=True)
            directory = repo / selector
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError(f'team not found or is a symlink: {selector}')
            # os.walk does not follow directory symlinks. Explicitly refuse them
            # rather than silently producing an incomplete or escaped team.
            members = sorted(directory.rglob('*.md'))
            if any(path.is_symlink() and path.is_dir() for path in directory.rglob('*')):
                raise ValueError(f'team contains a symlinked directory: {selector}')
            candidates = [path.relative_to(repo) for path in members
                          if path.stem.lower() not in {'readme', 'agents', 'contributing', 'license'}
                          and not any(part.startswith('.') for part in path.relative_to(repo).parts)]
            if not candidates:
                raise ValueError(f'team contains no agent profiles: {selector}')
        else:
            parts = _selection_parts(selector)
            filename = Path(parts[-1])
            if filename.suffix and filename.suffix != '.md':
                raise ValueError('profile must use team/profile or team/profile.md')
            relative = Path(*parts)
            candidates = [relative if filename.suffix == '.md' else relative.with_suffix('.md')]
        for relative in candidates:
            path = _contained_file(repo, relative)
            if path in seen:
                continue
            seen.add(path)
            if path.stat().st_size > 512 * 1024:
                raise ValueError('profile exceeds the 512 KiB input limit')
            source = path.read_text(encoding='utf-8')
            profiles.append(parse_profile_document(source, str(path.relative_to(repo)), selector))
    return profiles


def load_profile_snapshot(path: Path, relative_path: str) -> Profile:
    try:
        source = path.read_text(encoding='utf-8')
    except OSError:
        raise ValueError(f'{path}: saved profile snapshot is missing or unreadable') from None
    profile = parse_profile_document(source, relative_path, str(path))
    if path.stat().st_size > 512 * 1024:
        raise ValueError(f'{path}: saved profile snapshot exceeds the 512 KiB input limit')
    return profile


def load_model_config(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        raise ValueError('model config must be readable JSON (not JSON5); pass --model-config '
                         'or prepare ~/.openclaw/openclaw.json') from None
    if not isinstance(raw, dict):
        raise TypeError('--model-config must contain an object')
    agents = raw.get('agents', {})
    defaults = agents.get('defaults', {}) if isinstance(agents, dict) else {}
    model = defaults.get('model') if isinstance(defaults, dict) else None
    primary = model.get('primary') if isinstance(model, dict) else model
    if not isinstance(primary, str) or not primary.strip():
        raise ValueError('--model-config requires agents.defaults.model or model.primary')
    models = raw.get('models', {})
    if not isinstance(models, dict):
        raise TypeError('models must be an object')
    selected = {'models': models, 'model': model}
    if 'models' in defaults:
        if not isinstance(defaults['models'], dict):
            raise ValueError('agents.defaults.models must be an object')
        selected['model_catalog'] = defaults['models']
    return selected


def build_config(profile: Profile, state: Path, port: int, model: dict, gateway_auth_value: str) -> dict:
    defaults = {
        'model': model['model'], 'workspace': str(state / 'workspace'),
        'skipBootstrap': True,
        'bootstrapMaxChars': max(20000, len(profile.body) + 4096),
        'bootstrapTotalMaxChars': max(24000, len(profile.body) + 8192),
    }
    if 'model_catalog' in model:
        defaults['models'] = model['model_catalog']
    return {
        'models': model['models'],
        'agents': {'defaults': defaults, 'list': [{
            'id': 'main', 'default': True, 'name': profile.name,
            'workspace': str(state / 'workspace'),
            'agentDir': str(state / 'agents/main/agent'),
        }]},
        'bindings': [{'agentId': 'main', 'match': {'channel': 'bcs'}}],
        'gateway': {'mode': 'local', 'port': port, 'bind': 'loopback',
                    'auth': {'mode': 'token', GATEWAY_AUTH_FIELD: gateway_auth_value}},
        'browser': {'enabled': False},
    }


def identity_files(profile: Profile) -> dict[str, str]:
    return {
        # Preserve all mission/workflow/code sections, including unusual Markdown.
        'SOUL.md': profile.body,
        'IDENTITY.md': f'# {profile.emoji} {profile.name}\n\n{profile.description}\n',
        'AGENTS.md': (
            '# Role workspace\n\n'
            'Read SOUL.md for your complete role, responsibilities and workflow.\n'
            'Use IDENTITY.md for your name and role summary.\n'
            'In BCS conversations, follow the supplied session context and use the\n'
            'available BCS coordination tools when needed. Role descriptions do not\n'
            'grant tool access or permissions that the runtime has not provided.\n'
        ),
    }
