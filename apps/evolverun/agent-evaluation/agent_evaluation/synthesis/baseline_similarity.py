#!/usr/bin/env python3
"""Local reference-only character n-gram retrieval, not a semantic duplicate verdict."""
import argparse,hashlib,json,math,re,sys,unicodedata
from collections import Counter
from pathlib import Path
class SimilarityError(Exception):pass

def chars(s):
    x=''.join(c for c in unicodedata.normalize('NFKC',s).casefold() if not c.isspace())
    if not x:return []
    n=min(3,len(x));return [x[i:i+n] for i in range(len(x)-n+1)]
def search(path,expected_sha,query,top_k=10):
    if not isinstance(query,str) or not chars(query) or len(query)>10000:raise SimilarityError('INVALID_QUERY')
    if not isinstance(expected_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_sha):raise SimilarityError('INVALID_BASELINE_SHA')
    if type(top_k) is not int or not 1<=top_k<=100:raise SimilarityError('INVALID_TOP_K')
    docs=[];ids=set();digest=hashlib.sha256();size=0;df=Counter()
    with open(path,'rb') as f:
        for line in f:
            size+=len(line)
            if size>100_000_000 or len(line)>2_000_000:raise SimilarityError('BASELINE_TOO_LARGE')
            digest.update(line)
            try:x=json.loads(line)
            except (ValueError,UnicodeDecodeError) as e:raise SimilarityError('INVALID_JSONL') from e
            if not isinstance(x,dict) or not isinstance(x.get('id'),str) or not x['id'] or not isinstance(x.get('question'),str) or not isinstance(x.get('tags'),list) or any(not isinstance(t,str) for t in x['tags']):raise SimilarityError('INVALID_ROW')
            if x['id'] in ids:raise SimilarityError('DUPLICATE_ID')
            ids.add(x['id']);counts=Counter(chars(x['question']));df.update(counts.keys());docs.append((x['id'],x['tags'],counts))
    if digest.hexdigest()!=expected_sha:raise SimilarityError('BASELINE_HASH_MISMATCH')
    N=len(docs);idf=lambda g:math.log((N+1)/(df.get(g,0)+1))+1
    q=Counter(chars(query));qv={g:(1+math.log(c))*idf(g) for g,c in q.items()};qn=math.sqrt(sum(v*v for v in qv.values()));res=[]
    for ident,tags,terms in docs:
        dv={g:(1+math.log(c))*idf(g) for g,c in terms.items()};dn=math.sqrt(sum(v*v for v in dv.values()));score=sum(w*dv.get(g,0) for g,w in qv.items())/(qn*dn) if dn else 0
        if score>0:res.append({'id':ident,'score':round(score,6),'tags':tags})
    res.sort(key=lambda x:(-x['score'],x['id']))
    return {'schema':'synthesis.baseline-similarity.v1','baseline_sha256':expected_sha,'records_scanned':N,'matching':'character_trigram_tfidf_cosine_all_categories','results':res[:top_k],'scope_notes':['candidate_reference_only','lexical_similarity_not_semantic_verdict','no_guarantee_of_no_duplicates','scores_are_not_probability','all_categories_scanned']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',required=True);p.add_argument('--sha256',required=True);p.add_argument('--query-file',required=True);p.add_argument('--top-k',type=int,default=10);a=p.parse_args()
    try:
        f=Path(a.query_file)
        if f.stat().st_size>40000:raise SimilarityError('QUERY_TOO_LARGE')
        print(json.dumps(search(a.baseline,a.sha256,f.read_text(encoding='utf8'),a.top_k),ensure_ascii=False,sort_keys=True));return 0
    except (OSError,ValueError,TypeError,SimilarityError) as e:
        print(json.dumps({'error':str(e) if isinstance(e,SimilarityError) else type(e).__name__}),file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
