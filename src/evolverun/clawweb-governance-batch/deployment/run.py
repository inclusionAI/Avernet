#!/usr/bin/env python3
"""Daily deployment composition root. --check never queries data or invokes a model."""
import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from clawweb_batch.adapters.http import ClawWebHTTP, JsonHTTP
from clawweb_batch.adapters.mounts import prepare_readonly_mounts, verify_readonly_roots
from clawweb_batch.adapters.nas import NASReader
from clawweb_batch.artifacts import run_lock, write_json
from clawweb_batch.config import load_config, load_deployment, load_llm
from clawweb_batch.core import select_watermark, day_range
from clawweb_batch.pipeline import run, apply_requests, write_summary, safe_error
from openclaw_analysis import OpenClawAnalysis
from readiness import ReadyData, JournalCenter, valid_day

BINARY = '/usr/bin/openclaw'
NODE_DIRECTORY = '/opt/node/bin'
TZ = timezone(timedelta(hours=8))


class TaskStopped(BaseException):
    """Propagate interruption through adapters that catch ordinary business failures."""



def integrity_check():
    for path in (ROOT.parent/'clawweb_batch/pipeline.py', ROOT/'openclaw_analysis.py'):
        if not path.is_file():
            raise ValueError('incomplete deployment package')


def static_check(cfg, runtime_root=ROOT):
    integrity_check()
    for name in ('yaml','odps','pypai'):
        if importlib.util.find_spec(name) is None:
            raise ValueError('missing preinstalled runtime module: '+name)
    for name in ('clawweb-governance','clawweb-verification'):
        if not (ROOT.parents[1]/'clawweb-skills/clawinsight-skills'/name/'SKILL.md').is_file():
            raise ValueError('missing project Skill: '+name)
    llm = load_llm(cfg.llm_config_file)
    verify_readonly_roots(cfg.nas_roots)
    node = subprocess.run([NODE_DIRECTORY+'/node','--version'],capture_output=True,text=True,check=True,timeout=15)
    version = subprocess.run([BINARY,'--version'],capture_output=True,text=True,check=True,timeout=30)
    if '2026.7.1-2' not in version.stdout or not node.stdout.strip().startswith('v22.'):
        raise ValueError('runtime differs from verified OpenClaw/Node image contract')
    for path in (cfg.output_dir,cfg.state_dir):
        if not path.is_relative_to(runtime_root):
            raise ValueError('runtime writes must stay within this independent deployment')
    return llm, {'status':'STATIC_READY','node':node.stdout.strip(),'openclaw':'2026.7.1-2',
                  'model':llm['model'],'project':cfg.project,'effect_center':cfg.clawweb_url,
                  'nas_readonly':True,'allow_writes':cfg.allow_writes,
                  'data_queries_executed':False,'model_invoked':False,'business_writes':0,
                  'unverified':['current dependency dates','ODPS read permissions','effect-center API access','formal business outputs']}


def cli(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config.json')
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--check',action='store_true')
    modes.add_argument('--dry-run',action='store_true')
    modes.add_argument('--apply',action='store_true')
    parser.add_argument('--date',help='optional YYYYMMDD historical replay; dry-run only')
    parser.add_argument('--top-bots','--top-per-lane',dest='top_per_lane',type=int,help='maximum Cron user+bot investigation slots')
    args=parser.parse_args(argv)
    if args.date and not valid_day(args.date):
        parser.error('--date must be a calendar date YYYYMMDD')
    if args.apply and args.date:
        parser.error('historical replay is dry-run only')
    if args.top_per_lane is not None and not 1<=args.top_per_lane<=50:
        parser.error('--top-per-lane must be 1..50')
    if args.check and (args.date or args.top_per_lane):
        parser.error('--check cannot select business input')
    return args


def main(argv=None):
    args=cli(argv)
    os.umask(0o077)
    logging.getLogger('odps').setLevel(logging.ERROR)
    output=None
    report=None
    try:
        cfg=load_config(args.config.resolve())
        if args.apply and not cfg.allow_writes:
            raise PermissionError('formal writes remain disabled in this deployment; review before enabling allow_writes')
        if not cfg.cron_only:
            raise ValueError('this deployment requires cron_only=true; non-Cron governance is disabled')
        if args.check:
            _,summary=static_check(cfg, args.config.resolve().parent)
            print(json.dumps(summary,ensure_ascii=False),flush=True)
            return 0
        # A fresh scheduled container may need its existing trusted NAS sources attached read-only.
        integrity_check()
        prepare_readonly_mounts(load_deployment(cfg.llm_config_file).get('nas',{}),cfg.nas_roots)
        llm,_=static_check(cfg, args.config.resolve().parent)
        now=datetime.now(TZ)
        run_id=now.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
        output=cfg.output_dir/run_id
        output.mkdir(parents=True,exist_ok=False)
        # Keep cross-run cache, exclusion lock and publication journal stable; isolate every output.
        run_cfg=replace(cfg,output_dir=output/'analysis')
        started=time.monotonic()
        def interrupted(signum,_frame):
            raise TaskStopped('interrupted or total task time limit reached')
        for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGALRM):
            signal.signal(sig,interrupted)
        signal.alarm(3600)
        with run_lock(cfg.state_dir):
            from pypai.utils import env_utils
            source=ReadyData(env_utils.get_odps_instance(),cfg.project,(now-timedelta(days=1)).strftime('%Y%m%d'),cron_only=cfg.cron_only)
            source.validate_schema()
            begin=(now-timedelta(days=cfg.lookback_days-1)).strftime('%Y%m%d')
            counts=source.daily_counts(begin,now.strftime('%Y%m%d'))
            watermark=select_watermark(counts,now.strftime('%Y%m%d'),cfg.max_data_age_days,cron_only=cfg.cron_only)
            if args.date and args.date not in watermark['paired_days']:
                raise ValueError('requested day is not ready across all three dependency tables')
            if args.apply:
                end=watermark['end_date']
                start=(datetime.strptime(end,'%Y%m%d')-timedelta(days=cfg.window_days-1)).strftime('%Y%m%d')
                if set(day_range(start,end))-set(watermark['paired_days']):
                    raise ValueError('formal writes blocked by incomplete dependency window')
            write_json(output/'readiness.json',source.readiness)
            transport=ClawWebHTTP(cfg.clawweb_url,JsonHTTP(cfg.timeout_seconds),args.apply and cfg.allow_writes)
            center=JournalCenter(transport,cfg.state_dir/'publication-journal')
            analyst=OpenClawAnalysis(llm,ROOT.parents[1]/'clawweb-skills/clawinsight-skills',output/'openclaw',BINARY,NODE_DIRECTORY,
                                    started+3600,{},max_tokens=cfg.analysis_max_tokens)
            nas=NASReader(cfg.nas_roots)
            print(json.dumps({'event':'started','mode':'apply' if args.apply else 'dry-run',
                'data_date':args.date or watermark['end_date'],'run_dir':str(output)},ensure_ascii=False),flush=True)
            # Complete every read and both analysis stages before any allowed effect-center mutation.
            report=run(run_cfg,source,center,analyst,nas,now=now,apply=False,
                       top=args.top_per_lane,end_date=args.date)
            report['requested_mode']='apply' if args.apply else 'dry-run'
            report['dependency_readiness']=source.readiness
            if not report['errors']:
                for plan in report['verification']:
                    print(f"[verification] lane={plan['lane']} item={plan['payload']['improvementId']} outcome={plan['payload']['outcome']}",flush=True)
                    evidence=json.loads((Path(report['output_dir'])/plan['evidence_file']).read_text())
                    try:
                        explanation=analyst.explain(evidence,plan)
                        write_json(output/'verification-explanations'/f"{plan['lane']}-{plan['payload']['improvementId']}.json",explanation)
                        plan['skill_explanation']=explanation
                    except Exception as exc:
                        report['errors'].append({'stage':'verification-skill','improvementId':plan['payload']['improvementId'],
                                                 'error_type':type(exc).__name__,'message':safe_error(exc)})
                        continue
            report['model_calls']=analyst.calls
            if args.apply and not report['errors']:
                report['mode']='apply'
                apply_requests(report,run_cfg,center,output)
            elif args.apply:
                report['write_status']='BLOCKED_BY_ERRORS'
            report['status']='FAILED' if report['errors'] else 'SUCCEEDED'
            report['finished_at']=datetime.now(TZ).isoformat()
            report['run_dir']=str(output)
            report['governance_selected']=len(report['ranking'])
            report['verification_selected']=len(report['verification'])
            # No selected evidence means no LLM invocation; never claim either Skill ran in that case.
            write_json(output/'run.json',report)
            write_json(output/'requests.json',report['requests'])
            write_summary(output/'review.md',report)
            write_json(cfg.output_dir/'latest.json',{'run_dir':str(output),'status':report['status'],
                'data_date':report['watermark']['end_date'],'mode':report['requested_mode']})
            print(json.dumps({k:report[k] for k in ('status','requested_mode','run_dir','model_calls',
                'governance_selected','verification_selected','external_writes','errors')},ensure_ascii=False),flush=True)
            return 0 if report['status']=='SUCCEEDED' else 2
    except (Exception,TaskStopped) as exc:
        error={'status':'FAILED','error_type':type(exc).__name__,'message':safe_error(exc),
               'formal_run_started':output is not None,'run_dir':str(output) if output else None}
        if output:
            write_json(output/'failure.json',error)
            write_json(cfg.output_dir/'latest.json',{'run_dir':str(output),'status':'FAILED',
                'mode':'apply' if args.apply else 'dry-run'})
        print(json.dumps(error,ensure_ascii=False),flush=True)
        return 2
    finally:
        signal.alarm(0)


if __name__=='__main__':
    sys.exit(main())
