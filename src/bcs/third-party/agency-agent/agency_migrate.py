#!/usr/bin/env python3
"""Offline migration of flat OpenClaw instances into engine-scoped state.

No network requests or runtime commands. Preview by default; --apply stages and
verifies copies, retains original trees as private backups, and writes a journal.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from agency_console import report
from agency_runtime import private_dir, read_json, state_lock, write_json


def signature(root: Path) -> dict[str, tuple[str, str]]:
    """Hash regular files and link text without traversing external symlinks."""
    result = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = ('link', os.readlink(path))
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                result[relative] = ('file', digest.hexdigest())
    return result


def source_roots(values: list[Path]) -> list[Path]:
    roots = []
    for value in values:
        path = value.expanduser().resolve()
        if path.exists() and path not in roots:
            if not path.is_dir():
                raise ValueError('migration source must be a directory')
            roots.append(path)
    return roots


def free_port(port: int) -> None:
    for candidate in range(port, port + 20):
        with socket.socket() as listener:
            try:
                listener.bind(('127.0.0.1', candidate))
            except OSError:
                raise ValueError(f'port {candidate} is in use; stop affected Gateways before migrating') from None


def build_plan(sources: list[Path], target: Path, engine: str) -> list[dict]:
    engine_root = target / engine
    if engine_root.is_symlink():
        raise ValueError('target engine directory must not be a symlink')
    occupied = []
    for path in engine_root.glob('*/instance.json'):
        occupied.append(read_json(path)['port'])
    entries = []
    destinations = set()
    for root in sources:
        if target == root or target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError('source and target roots must be separate, non-nested directories')
        for metadata in sorted(root.glob('*/instance.json')):
            source = metadata.parent
            record = read_json(metadata)
            if record.get('engine', 'openclaw') != engine:
                continue
            if source.is_symlink() or metadata.is_symlink():
                raise ValueError('source instance directories/metadata must not be symlinks')
            if any(source.glob('*pending*.json')):
                raise ValueError(f'{source.name}: resolve pending registration/metadata before migration')
            destination = engine_root / source.name
            if destination.exists() or destination.is_symlink() or destination in destinations:
                raise ValueError(f'{source.name}: destination conflict; refusing to overwrite either identity')
            port = record.get('port')
            if type(port) is not int or not 1024 <= port <= 65515:
                raise ValueError(f'{source.name}: invalid saved port')
            free_port(port)
            new_port = port
            while any(abs(new_port - used) < 20 for used in occupied):
                new_port = max(occupied) + 20
            if new_port > 65515:
                raise ValueError('no room for migrated Gateway ports')
            free_port(new_port)
            occupied.append(new_port)
            destinations.add(destination)
            entries.append({'source': str(source), 'destination': str(destination),
                            'old_port': port, 'port': new_port, 'engine': engine})
    return entries


def rebase_string(value: str, mapping: dict[str, str]) -> str:
    for source, destination in sorted(mapping.items(), key=lambda item: -len(item[0])):
        if value == source or value.startswith(source + '/'):
            return destination + value[len(source):]
    # macOS /var and /private/var may name the same legacy instance.
    if value.startswith('/'):
        canonical = str(Path(value).resolve())
        for source, destination in sorted(mapping.items(), key=lambda item: -len(item[0])):
            if canonical == source or canonical.startswith(source + '/'):
                return destination + canonical[len(source):]
    return value


def rebase_document(value, mapping: dict[str, str]):
    if isinstance(value, dict):
        protected = {'token', 'bot_token', 'apikey', 'api_key', 'password', 'secret', 'clientsecret'}
        return {rebase_string(key, mapping): (child if key.lower() in protected
                else rebase_document(child, mapping)) for key, child in value.items()}
    if isinstance(value, list):
        return [rebase_document(child, mapping) for child in value]
    return rebase_string(value, mapping) if isinstance(value, str) else value


def prepare_copy(entry: dict, stage: Path, mapping: dict[str, str]) -> None:
    source = Path(entry['source'])
    shutil.copytree(source, stage, symlinks=True)
    if signature(source) != signature(stage):
        raise ValueError(f'{source.name}: staged copy verification failed')
    managed = {stage / 'instance.json', stage / 'openclaw.json'}
    managed.update((stage / 'plugins').rglob('*.json'))
    managed.update(stage.glob('agents/*/sessions/sessions.json'))
    for path in sorted(managed):
        if not path.exists():
            continue
        if path.is_symlink():
            raise ValueError(f'{source.name}: managed metadata must not be a symlink')
        before = read_json(path)
        after = rebase_document(before, mapping)
        if path == stage / 'instance.json':
            after['engine'] = entry['engine']
            after['port'] = entry['port']
        if path == stage / 'openclaw.json':
            after.setdefault('gateway', {})['port'] = entry['port']
        if after != before:
            write_json(path, after)
    for parent, directories, files in os.walk(stage, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            if not path.is_symlink():
                continue
            original = os.readlink(path)
            if os.path.isabs(original):
                replacement = rebase_string(original, mapping)
            else:
                source_link = source / path.relative_to(stage)
                resolved = (source_link.parent / original).resolve()
                replacement = (original if resolved.is_relative_to(source)
                               else rebase_string(str(resolved), mapping))
            if replacement != original:
                path.unlink()
                path.symlink_to(replacement)
    for path in (stage / '.bcs').rglob('*'):
        if path.is_symlink():
            raise ValueError('credential directories/files must not be symlinks')
        if path.is_file():
            path.chmod(0o600)
    session = source / '.bcs/session.json'
    if session.exists() and session.read_bytes() != (stage / '.bcs/session.json').read_bytes():
        raise ValueError('session changed during migration')
    private_dir(stage)


def apply_migration(sources: list[Path], target: Path, engine: str) -> tuple[int, Path | None]:
    with ExitStack() as locks:
        for root in sorted([*sources, target]):
            locks.enter_context(state_lock(root))
        locks.enter_context(state_lock(target / engine))
        entries = build_plan(sources, target, engine)
        if not entries:
            return 0, None
        candidates = [root / name for root in sources for name in ('agency-agent', 'agency-agents')
                      if (root / name / '.git').exists()]
        cache = target / 'agency-agent'
        if cache.is_symlink():
            raise ValueError('shared checkout must not be a symlink')
        if cache.exists() and not (cache / '.git').exists():
            raise ValueError('shared checkout destination is not a Git repository')
        mapping = {entry['source']: entry['destination'] for entry in entries}
        mapping.update({str(root / name): str(cache) for root in sources
                        for name in ('agency-agent', 'agency-agents')})
        backup_root = target / '.migration-backups' / str(time.time_ns())
        private_dir(backup_root)
        journal = {'status': 'preparing', 'engine': engine, 'instances': entries}
        journal_path = backup_root / 'manifest.json'
        for index, entry in enumerate(entries):
            entry['backup'] = str(backup_root / f'{index:03d}-{Path(entry["source"]).name}')
        write_json(journal_path, journal)
        created = []
        moved = []
        cache_created = False
        with tempfile.TemporaryDirectory(prefix='.migration-stage-', dir=target) as temporary:
            staging = Path(temporary)
            try:
                if not cache.exists() and candidates:
                    shutil.copytree(candidates[0], staging / 'checkout', symlinks=True)
                    if signature(candidates[0]) != signature(staging / 'checkout'):
                        raise ValueError('shared checkout copy verification failed')
                for index, entry in enumerate(entries):
                    prepare_copy(entry, staging / str(index), mapping)
                journal['status'] = 'committing'
                write_json(journal_path, journal)
                if (staging / 'checkout').exists():
                    (staging / 'checkout').rename(cache)
                    cache_created = True
                for index, entry in enumerate(entries):
                    destination = Path(entry['destination'])
                    (staging / str(index)).rename(destination)
                    created.append(destination)
                    source, backup = Path(entry['source']), Path(entry['backup'])
                    source.rename(backup)
                    moved.append((source, backup))
                    if (backup / '.bcs/session.json').exists():
                        if (backup / '.bcs/session.json').read_bytes() != (destination / '.bcs/session.json').read_bytes():
                            raise ValueError('post-move session verification failed')
                journal['status'] = 'complete'
                write_json(journal_path, journal)
            except BaseException:
                # Restore original trees; delete only copies created by this attempt.
                for source, backup in reversed(moved):
                    backup.rename(source)
                for destination in reversed(created):
                    shutil.rmtree(destination)
                if cache_created:
                    shutil.rmtree(cache)
                journal['status'] = 'rolled_back'
                write_json(journal_path, journal)
                raise
        return len(entries), journal_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, action='append', help='legacy flat state root; repeatable')
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.avernet/bcs/agency-agent')
    parser.add_argument('--engine', choices=['openclaw'], default='openclaw')
    parser.add_argument('--apply', action='store_true', help='apply migration; default is read-only preview')
    args = parser.parse_args()
    defaults = [Path.home() / '.avernet/bcs/third-party', Path.home() / '.bcs/agency',
                Path.home() / '.local/share/avernet/agency']
    try:
        sources = source_roots(args.source_dir if args.source_dir is not None else defaults)
        target = args.state_dir.expanduser().resolve()
        entries = build_plan(sources, target, args.engine)
        report(f'Migration preview: {len(entries)} instance(s), engine={args.engine}')
        for entry in entries:
            report(f'  {entry["source"]} -> {entry["destination"]}; port {entry["old_port"]} -> {entry["port"]}')
        if args.apply:
            count, journal = apply_migration(sources, target, args.engine)
            report(f'Migrated {count} instance(s); original trees retained in private backups.', 'success')
            if journal is not None:
                report(f'Private migration journal: {journal}')
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        message = str(error) if not isinstance(error, OSError) else type(error).__name__
        report(f'ERROR: {message}', 'error', error=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
