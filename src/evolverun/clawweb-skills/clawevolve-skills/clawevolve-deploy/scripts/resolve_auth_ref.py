#!/usr/bin/env python3
"""resolve auth_ref from system config"""
import json, os, sys, argparse

def find_auth_refs(obj, parts=None):
    if parts is None: parts = []
    r = []
    if isinstance(obj, dict):
        if 'auth_ref' in obj and len(obj) == 1: r.append((list(parts), obj['auth_ref']))
        else:
            for k, v in obj.items(): r.extend(find_auth_refs(v, parts + [k]))
    elif isinstance(obj, list):
        for i, v in enumerate(obj): r.extend(find_auth_refs(v, parts + [str(i)]))
    return r

def getp(obj, parts):
    for p in parts:
        if isinstance(obj, dict) and p in obj: obj = obj[p]
        else: return None
    return obj

def _replace(server, fp, val):
    cur = server
    for p in fp[:-1]:
        if isinstance(cur, dict) and p in cur: cur = cur[p]
        else: return
    if isinstance(cur, dict) and fp[-1] in cur: cur[fp[-1]] = val

def resolve(proj, sys_cfg, dry=False):
    with open(proj) as f: project = json.load(f)
    if not os.path.exists(sys_cfg):
        print("system config not found"); return False
    with open(sys_cfg) as f: system = json.load(f)
    refs = find_auth_refs(project)
    if not refs:
        print("[resolve_auth_ref] \u2705 No auth_ref placeholders")
        return True
    print(f"[resolve_auth_ref] Found {len(refs)} auth_ref(s):")
    proj_sv = project.get('mcpServers', {})
    sys_sv = system.get('mcpServers', {})
    resolved = warnings = 0
    for parts, ri in refs:
        sc = ri.get('server_code',''); fields = ri.get('fields',[])
        print(f"  {'.'.join(parts)} \u2192 {sc}")
        if sc not in proj_sv: warnings += 1; continue
        ps = proj_sv[sc]; ss = sys_sv.get(sc, {})
        for fld in fields:
            fp = fld.split('.'); rv = getp(ss, fp)
            if rv and isinstance(rv, str):
                if not dry: _replace(ps, fp, rv); resolved += 1
                print(f"    \u2705 {fld} resolved")
            else:
                warnings += 1
    if not dry:
        with open(proj, 'w') as f: json.dump(project, f, indent=2, ensure_ascii=False)
        if warnings > 0:
            print(f"[resolve_auth_ref] \u26a0\ufe0f WARNING: {warnings} auth_ref(s) could not be resolved — MCP services may be partially unavailable", file=__import__('sys').stderr)
        print(f"[resolve_auth_ref] \u2705 Written ({resolved} resolved, {warnings} warnings)")
    return warnings == 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', default=os.path.expanduser('~/.openclaw/workspace'))
    p.add_argument('--system-config', default=os.path.expanduser('~/.mcporter/mcporter.json'))
    p.add_argument('--dry-run', action='store_true')
    a = p.parse_args()
    proj = os.path.join(a.workspace, 'config', 'mcporter.json')
    if not os.path.exists(proj): sys.exit(0)
    sys.exit(0 if resolve(proj, a.system_config, a.dry_run) else 1)

if __name__ == '__main__': main()
