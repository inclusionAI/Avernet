"""Independent typed predicates: one live response check per frozen obligation."""
import json

_MISSING = object()


def field(value, path):
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def resolve(value, context):
    if isinstance(value, str) and value.startswith('$'):
        return context.get(value[1:], _MISSING)
    return value


def equivalent(actual, expected):
    """JSON equality without Python's bool/int equivalence or missing/null alias."""
    if actual is _MISSING or expected is _MISSING or type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(equivalent(actual[k], v) for k, v in expected.items())
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(equivalent(a, b) for a, b in zip(actual, expected))
    return actual == expected


def evaluate(row, status, body, context):
    """No response payload or secret value escapes; evidence is the predicate result."""
    kind = row['kind']
    expected = resolve(row.get('expected'), context)
    if kind == 'status':
        return equivalent(status, expected)
    if kind == 'eq':
        return equivalent(field(body, row['path']), expected)
    if kind == 'nonempty':
        value = field(body, row['path'])
        return isinstance(value, str) and bool(value.strip())
    if kind == 'contains':
        return isinstance(body, dict) and expected in json.dumps(body, ensure_ascii=True)
    if kind == 'keys':
        return isinstance(body, dict) and sorted(body) == sorted(expected)
    if kind == 'projection':
        return equivalent(body, expected)
    if kind == 'membership':
        rows = field(body, row['path'])
        target = resolve(row['target'], context)
        if not isinstance(rows, list) or target is _MISSING or any(not isinstance(r, dict) for r in rows):
            return False
        # Missing identifier is malformed evidence, not proof of an absent member.
        if any(not isinstance(r.get(row['field']), str) for r in rows):
            return False
        found = any(equivalent(r[row['field']], target) for r in rows)
        return equivalent(found, expected)
    return False
