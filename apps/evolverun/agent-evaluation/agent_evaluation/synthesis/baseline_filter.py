#!/usr/bin/env python3
"""Bounded local historical-candidate selection; no writes and no model calls.

Filters are lexical/exact. An unmapped historical tag must not be treated as
proof that it is unrelated to a requested target. This is not semantic dedup.
"""
import argparse,hashlib,json,re,sys,unicodedata
from pathlib import Path
MAX_LINE=2_000_000
MAX_BASELINE=100_000_000
MAX_RESULTS=5000
SHA=re.compile(r'[0-9a-f]{64}\Z')
class FilterError(Exception):pass

def norm(s):return unicodedata.normalize('NFKC',s).casefold().strip()
def strings(value,name,allow_empty=False):
    if not isinstance(value,list) or (not value and not allow_empty):raise FilterError('INVALID_'+name)
    if any(not isinstance(s,str) or not norm(s) for s in value):raise FilterError('INVALID_'+name)
    return value

def run(source,expected_sha,request):
    if not isinstance(expected_sha,str) or not SHA.fullmatch(expected_sha):raise FilterError('INVALID_BASELINE_SHA')
    if not isinstance(request,dict):raise FilterError('INVALID_REQUEST')
    allowed={'target_tags','tag_aliases','query_terms_any','exclude_tags','exclude_question_terms','limit'}
    if set(request)-allowed:raise FilterError('UNKNOWN_REQUEST_FIELD')
    target=strings(request.get('target_tags'),'TARGET_TAGS'); aliases=request.get('tag_aliases',{})
    if not isinstance(aliases,dict) or any(k not in target for k in aliases):raise FilterError('INVALID_ALIASES')
    expanded=set(target)
    for items in aliases.values():expanded.update(strings(items,'ALIASES'))
    terms=strings(request.get('query_terms_any',[]),'QUERY_TERMS',True)
    excludes=set(strings(request.get('exclude_tags',[]),'EXCLUDE_TAGS',True))
    exclude_terms=strings(request.get('exclude_question_terms',[]),'EXCLUDE_TERMS',True)
    limit=request.get('limit',100)
    if type(limit) is not int or not 1<=limit<=MAX_RESULTS:raise FilterError('INVALID_LIMIT')
    rows=[]; ids=set();h=hashlib.sha256();size=0;seen=0;bad_alias_hits=0;excluded_tag=excluded_term=0
    with open(source,'rb') as f:
        for line in f:
            size+=len(line)
            if len(line)>MAX_LINE or size>MAX_BASELINE:raise FilterError('BASELINE_TOO_LARGE')
            h.update(line)
            try:row=json.loads(line)
            except (ValueError,UnicodeDecodeError) as e:raise FilterError('INVALID_JSONL') from e
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or not isinstance(row.get('question'),str) or not isinstance(row.get('tags'),list) or any(not isinstance(t,str) for t in row['tags']):raise FilterError('INVALID_ROW')
            if row['id'] in ids:raise FilterError('DUPLICATE_ID')
            ids.add(row['id']);seen+=1;tags=set(row['tags']); matched=tags & expanded
            if not matched:continue
            q=norm(row['question'])
            if terms and not any(norm(t) in q for t in terms):continue
            if tags & excludes:excluded_tag+=1;continue
            if any(norm(t) in q for t in exclude_terms):excluded_term+=1;continue
            is_alias=bool(matched - set(target));bad_alias_hits+=is_alias
            rows.append({'id':row['id'],'matched_historical_tags':sorted(matched),'alias_match':is_alias})
    actual=h.hexdigest()
    if actual!=expected_sha:raise FilterError('BASELINE_HASH_MISMATCH')
    rows.sort(key=lambda r:r['id'])
    return {'schema':'synthesis.baseline-filter-result.v1','baseline_sha256':actual,'records_scanned':seen,'matched_count':len(rows),'returned_count':min(len(rows),limit),'truncated':len(rows)>limit,'excluded_by_tag':excluded_tag,'excluded_by_literal_question_term':excluded_term,'rows':rows[:limit],'matching_policy':'exact_tag_and_caller_supplied_aliases_only','scope_notes':['candidate_reference_not_approved_coverage','unmapped_tags_not_scanned_as_target_matches','exclusion_is_literal_not_semantic','not_semantic_dedup','does_not_prove_absence_of_related_questions','all_baseline_must_be_used_for_global_dedup']}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True);ap.add_argument('--sha256',required=True);ap.add_argument('--request',required=True);a=ap.parse_args()
    try:
        p=Path(a.request)
        if p.stat().st_size>65536:raise FilterError('REQUEST_TOO_LARGE')
        request=json.loads(p.read_text(encoding='utf8'))
        print(json.dumps(run(a.baseline,a.sha256,request),ensure_ascii=False,sort_keys=True));return 0
    except (OSError,ValueError,TypeError,FilterError) as e:
        print(json.dumps({'error':str(e) if isinstance(e,FilterError) else type(e).__name__},ensure_ascii=False),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
