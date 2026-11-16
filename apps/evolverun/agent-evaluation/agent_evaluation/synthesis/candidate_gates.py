#!/usr/bin/env python3
"""Deterministic candidate gates. Semantic fixture labels are NOT a detector.

No I/O, model calls, publishing, or Actor execution. Unknown semantic review is
REVIEW_REQUIRED; only explicitly configured fixtures can use fixture labels.
"""
import hashlib
import json
import re
import unicodedata

FIELDS = {'id', 'question', 'tags', 'sources', 'answer', 'answer_criteria', 'process_criteria', 'difficulty'}
SOURCE_FIELDS = {'type', 'name', 'reference', 'observed_at', 'evidence'}
ANSWER_CRITERIA_FIELDS = {'criterion', 'max_score', 'full_credit', 'partial_credit', 'zero_credit'}
PROCESS_CRITERIA_FIELDS = ANSWER_CRITERIA_FIELDS | {'evidence'}
DIFFICULTY = {'evidence_location', 'rule_reasoning', 'information_linking', 'path_decision'}
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
DATE = re.compile(r'\d{4}-\d{2}-\d{2}(?:T[^\s]+)?\Z')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def fingerprint(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if not c.isspace() and unicodedata.category(c) != 'Cf')


def text(value, max_length=10000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= max_length


def shape_errors(candidate, known_tags):
    errors = []
    if not isinstance(candidate, dict) or set(candidate) != FIELDS:
        return ['META_FIELDS']
    if not isinstance(candidate['id'], str) or not ID.fullmatch(candidate['id']):
        errors.append('META_ID')
    for field in ('question', 'answer'):
        if not text(candidate[field]):
            errors.append('META_' + field.upper())
    tags = candidate['tags']
    if (not isinstance(tags, list) or not 1 <= len(tags) <= 20
            or any(not isinstance(t, str) or t not in known_tags or re.search(r'\s', t) for t in tags)
            or len(tags) != len(set(t for t in tags if isinstance(t, str)))):
        errors.append('META_TAGS')
    sources = candidate['sources']
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        errors.append('META_SOURCES')
    else:
        for s in sources:
            if not isinstance(s, dict) or set(s) != SOURCE_FIELDS:
                errors.append('SOURCE_FIELDS'); continue
            if any(not text(s[k], 10000) for k in SOURCE_FIELDS):
                errors.append('SOURCE_EMPTY_OR_TYPE'); continue
            if not DATE.fullmatch(s['observed_at']):
                errors.append('SOURCE_DATE')
    for field in ('answer_criteria', 'process_criteria'):
        rows = candidate[field]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
            errors.append(field.upper() + '_FIELDS'); continue
        valid = True
        required = ANSWER_CRITERIA_FIELDS if field == 'answer_criteria' else PROCESS_CRITERIA_FIELDS
        for row in rows:
            if (not isinstance(row, dict) or set(row) != required
                    or any(not text(row.get(k), 3000) for k in required - {'max_score'})
                    or type(row.get('max_score')) is not int or not 1 <= row['max_score'] <= 100):
                valid = False
        if not valid:
            errors.append(field.upper() + '_FIELDS')
        elif sum(x['max_score'] for x in rows) != 100:
            errors.append(field.upper() + '_SUM')
    difficulty = candidate['difficulty']
    if (not isinstance(difficulty, dict) or set(difficulty) != DIFFICULTY
            or any(not isinstance(v, dict) or set(v) != {'score', 'reason'}
                   or type(v.get('score')) is not int or not 0 <= v['score'] <= 3
                   or not text(v.get('reason'), 2000) for v in difficulty.values())):
        errors.append('META_DIFFICULTY')
    return sorted(set(errors))


def actor_payload(candidate):
    """Deliberately do not serialize the full candidate and then remove fields."""
    if not isinstance(candidate, dict) or not text(candidate.get('id'), 80) or not text(candidate.get('question')):
        raise ValueError('INVALID_ACTOR_INPUT')
    return {'id': candidate['id'], 'question': candidate['question']}


def novelty(candidate, baseline, baseline_available, accepted, fixture_reviews, fixture_mode, policy_sha256):
    if not baseline_available:
        return {'decision': 'review', 'reason': 'BASELINE_UNAVAILABLE'}
    q = normalized(candidate['question'])
    for other in list(baseline) + list(accepted):
        if q == normalized(other['question']):
            return {'decision': 'reject', 'reason': 'EXACT_DUPLICATE', 'matched_id': other['id']}
    # These labels only exercise controller branches. The Agent cannot supply
    # them as tool arguments, and no live/business mode accepts them.
    review = fixture_reviews.get(fingerprint(candidate)) if fixture_mode else None
    if not isinstance(review, dict):
        return {'decision': 'review', 'reason': 'SEMANTIC_REVIEW_REQUIRED'}
    if set(review) != {'decision', 'reason', 'policy_sha256'} or review['decision'] not in ('novel', 'same_focus', 'reworded', 'excluded_concept', 'uncertain'):
        return {'decision': 'review', 'reason': 'INVALID_FIXTURE_REVIEW'}
    if review['policy_sha256'] != policy_sha256:
        return {'decision': 'review', 'reason': 'FIXTURE_REVIEW_POLICY_MISMATCH'}
    decision = review['decision']
    if decision in ('same_focus', 'reworded', 'excluded_concept'):
        return {'decision': 'reject', 'reason': {'same_focus': 'SAME_FOCUS_DUPLICATE', 'reworded': 'REWORDED_DUPLICATE', 'excluded_concept': 'SEMANTIC_EXCLUSION_FIXTURE'}[decision], 'label_scope': 'fixture_only_not_semantic_detector'}
    if decision == 'uncertain':
        return {'decision': 'review', 'reason': 'SEMANTIC_REVIEW_REQUIRED'}
    return {'decision': 'fixture_novel', 'reason': 'TRUSTED_FIXTURE_LABEL', 'label_scope': 'fixture_only_not_semantic_detector'}


def assess(candidate, *, known_tags, target_tags, materials, read_sources, baseline,
           baseline_available, accepted, request_exclusions, project_exclusions,
           fixture_reviews, fixture_mode):
    errors = shape_errors(candidate, known_tags)
    if errors:
        return {'status': 'rejected', 'errors': errors, 'semantic_validated': False}
    if not set(candidate['tags']) & set(target_tags):
        errors.append('OUTSIDE_TARGET_TAGS')
    if any(candidate['id'] == c['id'] for c in accepted):
        errors.append('DUPLICATE_CANDIDATE_ID')
    by_reference = {m['reference']: (key, m) for key, m in materials.items()}
    for source in candidate['sources']:
        match = by_reference.get(source['reference'])
        if not match:
            errors.append('SOURCE_NOT_ALLOWLISTED'); continue
        key, material = match
        if key not in read_sources:
            errors.append('SOURCE_NOT_READ')
        if not material.get('complete') or not material.get('accessible'):
            errors.append('SOURCE_NOT_USABLE')
        if material.get('role') != 'fixture' and fixture_mode:
            errors.append('NON_FIXTURE_SOURCE')
        if any(source[k] != material[k] for k in ('type', 'name', 'reference', 'observed_at')):
            errors.append('SOURCE_VERSION_OR_IDENTITY_MISMATCH')
        if source['evidence'] not in material['body']:
            errors.append('SOURCE_EVIDENCE_MISMATCH')
    # Literal policy is deliberately conservative, applied to the whole draft,
    # including gold and source excerpts; it is NOT semantic concept matching.
    all_text = normalized(encoded(candidate).decode('utf8'))
    for level, policy in [('REQUEST', request_exclusions), ('PROJECT', project_exclusions)]:
        if set(policy['exclude_tags']) & set(candidate['tags']):
            errors.append(level + '_EXCLUDED_TAG')
        if any(normalized(word) in all_text for word in policy['exclude_phrases']):
            errors.append(level + '_EXCLUDED_PHRASE')
    if errors:
        return {'status': 'rejected', 'errors': sorted(set(errors)), 'semantic_validated': False}
    review = novelty(candidate, baseline, baseline_available, accepted, fixture_reviews, fixture_mode,
                     fingerprint({'request': request_exclusions, 'project': project_exclusions}))
    if review['decision'] == 'reject':
        return {'status': 'rejected', 'errors': [review['reason']], 'novelty': review, 'semantic_validated': False}
    if review['decision'] != 'fixture_novel':
        return {'status': 'review_required', 'errors': [review['reason']], 'novelty': review, 'semantic_validated': False}
    return {'status': 'fixture_acceptable', 'errors': [], 'novelty': review,
            'semantic_validated': False, 'business_ready': False, 'training_ready': False}


def allocate_quota(candidate, quotas, credits):
    """One candidate earns exactly one slot, even if it has multiple tags."""
    eligible = sorted(t for t in candidate['tags'] if t in quotas and credits.get(t, 0) < quotas[t])
    return eligible[0] if eligible else None
