"""Bound, offline-verifiable canonical reporting for the complete local Workspace.

Hashes detect accidental drift, not malicious rewriting by the filesystem owner.
No remote client, credential resolution, SQL write, recovery or cleanup occurs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .inventory import binding_matches_reviewed_release, executor_digest
from .ledger import Ledger, LedgerError
from .profile import fingerprint
from .run_state import RunStateError, read

SUITE = 'owned-local-workspace-v1'
CASE_ID = 'LOCAL-WORKSPACE-INCIDENT-ROUNDTRIP-01'
CONTRACT = 'owned-local-workspace-report-binding-v1'
COMPLETION = 'owned-local-workspace-completion-v1'
# Versioned report-v1 schema is independent of current executable inventories.
# Future business changes need a new report version, not edits to these readers.
V1_REQUIREMENTS = tuple('WORKSPACE.'+name for name in (
    'create','detail','patch','list','members','label','visibility',
    'cli-create-get','cli-add-member','cli-fuse','cli-status',
    'incident-group','participant-mode','workspace','routing-service',
    'messages','incident-delete'))
ALL_REQUIREMENTS = V1_REQUIREMENTS
STAGES = (
    ('api', 'LOCAL-WORKSPACE-API-01', 'LOCAL_WORKSPACE_API_PASS', V1_REQUIREMENTS[:7]),
    ('cli', 'LOCAL-WORKSPACE-CLI-01', 'LOCAL_WORKSPACE_CLI_PASS', V1_REQUIREMENTS[7:11]),
    ('incident', 'LOCAL-WORKSPACE-INCIDENT-01', 'LOCAL_WORKSPACE_INCIDENT_CONFIG_PASS', V1_REQUIREMENTS[11:15]),
    ('messages', 'LOCAL-WORKSPACE-MESSAGES-01', 'LOCAL_WORKSPACE_MESSAGES_PASS', V1_REQUIREMENTS[15:16]),
    ('delete', 'LOCAL-WORKSPACE-DELETE-01', 'LOCAL_WORKSPACE_DELETE_PASS', V1_REQUIREMENTS[16:17]),
)
V1_OPERATIONS = {
    'api': ('fixture-ceo-visibility-public','fixture-pm-visibility-public','fixture-eng-visibility-public',
            'group-create-primary','group-create-with-members','group-patch','group-member-add',
            'group-member-remove','group-label','group-visibility'),
    'cli': ('create-primary','get-primary','add-member','get-after-add','fuse','status-primary',
            'get-after-status','create-termination','get-before-terminate','terminate','get-termination'),
    'incident': ('incident-create','incident-detail','participant-mode','workspace-put','workspace-get',
                 'routing-policy-put','settings-patch','incident-readback'),
    'messages': ('persistent-message','live-chat','agent-callback'),
    'delete': ('incident-delete',),
}


def frozen_inventory():
    return {'version':1,'requirements':list(V1_REQUIREMENTS),
            'operations':{name:list(items) for name,items in V1_OPERATIONS.items()}}


LEDGERS = {'api':'workspace-resources.sqlite', 'cli':'workspace-cli-resources.sqlite',
           'incident':'workspace-incident-resources.sqlite'}
SOURCES = ('workspace-binding.json', 'workspace-incident-roundtrip-report.json', 'bcs-config.json', 'owned-bcs.json',
           'local-fixtures.json', 'workspace-fusion-context.json',
           *(f'local-workspace-{name}.json' for name, *_ in STAGES), *LEDGERS.values())
REPORT_NAME = 'report-'+CASE_ID+'.json'
OUTPUTS = ('reports/summary.json','reports/junit.xml','reports/summary.md')


def _require(condition, message):
    if not condition:
        raise RunStateError(message)


def _sha(path):
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=8*1024*1024,
                 'Workspace report source must be bounded regular unshared data')
        raw=stream.read(8*1024*1024+1)
    _require(len(raw)<=8*1024*1024,'Workspace report source exceeds byte limit')
    return hashlib.sha256(raw).hexdigest()


def _exclusive(path,value):
    raw=(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
    return hashlib.sha256(raw).hexdigest()


def _sources(root):
    names={name for name in SOURCES if (root/name).exists() or (root/name).is_symlink()}
    receipt=root/'workspace-fusion-context.json'
    if receipt.is_file() and not receipt.is_symlink():
        context=read(receipt)
        roles=context.get('roles',{})
        from .profile import identifier
        for role in ('PM','ENG'):
            if role in roles:
                bot_id=identifier(roles[role],'frozen fusion Bot ID')
                for leaf in ('SOUL.md','MEMORY.md'):
                    _require(not (root/'bots').is_symlink() and not (root/'bots'/bot_id).is_symlink(),
                             'Workspace fusion input directory cannot be a symlink')
                    name='bots/'+bot_id+'/'+leaf
                    if (root/name).exists() or (root/name).is_symlink():
                        names.add(name)
    return names


def _bound(root):
    _require(root.is_absolute() and not root.is_symlink() and root.is_dir(),'Workspace report requires its original owned directory')
    binding=read(root/'workspace-binding.json')
    _require(binding.get('contract')==CONTRACT and binding.get('suite')==SUITE
             and binding.get('case_id')==CASE_ID and binding.get('runtime_dir')==str(root)
             and binding.get('selected_requirement_ids')==list(V1_REQUIREMENTS)
             and binding.get('report_inventory')==frozen_inventory()
             and [row['id'] for row in binding['parent']['requirements']]==list(V1_REQUIREMENTS)
             and binding_matches_reviewed_release(binding.get('legacy_inventory', []))
             and binding.get('store_messages') is True,'Workspace complete frozen binding is invalid')
    return binding


def _ledger_valid(root, binding, name, journal, deleted):
    expected=[]
    for resource in journal.get('resources',{}).values():
        expected.extend([(resource['handle'],'group',resource['id'],None),
                         (resource['initial_session']['handle'],'session',resource['initial_session']['id'],resource['handle'])])
    with Ledger(root/LEDGERS[name],binding['run_id'],journal['binding_digest'],read_only=True) as ledger:
        rows=ledger.resource_snapshot()
    return (len(rows)==len(expected) and len(expected)==(4 if name in ('api','cli') else 2)
        and {(r['handle'],r['kind'],r['resource_id'],r['parent']) for r in rows}==set(expected)
        and all(r['origin']=='created' and r['attempt']==1 and r['case_id']==journal['case_id']
                and r['state']==('deleted' if deleted else 'live') for r in rows))


def _project(root,binding):
    flow=read(root/'workspace-incident-roundtrip-report.json')
    runtime=read(root/'owned-bcs.json')
    _require(flow.get('run_id')==binding['run_id'] and flow.get('case_id')==CASE_ID
             and flow.get('executor_digest')==binding['executor_digest'], 'Workspace flow binding differs')
    _require(runtime.get('run_id')==binding['run_id'] and runtime.get('binary_sha256')==binding['bcs_binary_sha256']
             and runtime==flow.get('shutdown'),'Workspace runtime evidence differs from execution')
    stages={name:read(root/f'local-workspace-{name}.json') for name,*_ in STAGES if (root/f'local-workspace-{name}.json').exists()}
    keys={'api':'case','cli':'cli_case','incident':'incident_case','messages':'message_case',
          'delete':'delete_case'}
    complete=True
    observed=[]
    for name,stage_id,status,requirements in STAGES:
        stage=stages.get(name,{})
        if stage:
            _require(stage.get('run_id')==binding['run_id'] and stage.get('case_id')==stage_id
                     and stage.get('executor_digest')==binding['executor_digest']
                     and stage==flow.get(keys[name]),'Workspace stage does not match its original journal')
        assertions=stage.get('assertions',[])
        observed.extend(assertions)
        complete = complete and (stage.get('status')==status
            and assertions==[{'id':r,'status':'PASS'} for r in requirements]
            and bool(stage.get('operations')) and all(op.get('status')=='ACKNOWLEDGED' for op in stage['operations']))
    expected=[{'id':r,'status':'PASS'} for r in binding['report_inventory']['requirements']]
    complete=complete and observed==expected and flow.get('requirements')==expected and set(SOURCES)<=_sources(root)
    if complete:
        api,cli,incident,messages,deleted=(stages[n] for n,*_ in STAGES)
        complete = (all(_ledger_valid(root,binding,n,stages[n],n=='incident') for n in LEDGERS)
            and cli.get('api_journal_digest')==fingerprint(api)
            and incident.get('api_journal_digest')==fingerprint(api) and incident.get('cli_journal_digest')==fingerprint(cli)
            and messages.get('incident_digest')==fingerprint(incident)
            and deleted.get('message_journal_digest')==fingerprint(messages))
        complete = complete and all(stages[n].get('storage_verification',{}).get('status')=='PASS' for n in ('api','cli','incident','messages','delete'))
        for name,operations in binding['report_inventory']['operations'].items():
            complete=complete and [op.get('operation') for op in stages[name]['operations']]==operations
        fixture_digest=fingerprint(read(root/'local-fixtures.json'))
        complete=complete and all(stages[n].get('fixture_digest')==fixture_digest for n in ('api','cli','incident'))
        context=read(root/'workspace-fusion-context.json')
        complete=complete and cli.get('context_digest')==fingerprint(context) and len(context.get('files',[]))==4
        for item in context.get('files',[]):
            role=item.get('role');leaf=item.get('name')
            _require(role in ('PM','ENG') and leaf in ('SOUL.md','MEMORY.md'),'Workspace context file outside fixed scope')
            path=root/'bots'/context['roles'][role]/leaf
            complete=complete and _sha(path)==item.get('sha256')
        complete = complete and (messages.get('receiver_state')=='CLOSED'
            and len(messages.get('wire_receipts',[]))==8
            and all(r.get('status')=='TRANSPORT_RECEIVED_ONLY' and r.get('success_ack_sent') is False for r in messages['wire_receipts'])
            and messages['storage_verification'].get('new_rows')==3
            and messages['storage_verification'].get('baseline_unchanged') is True)
        for name,resource in (('delete',incident['resources']['incident']),):
            d=stages[name]
            complete = complete and (d.get('group_id')==resource['id'] and d.get('initial_session_id')==resource['initial_session']['id']
                and d.get('not_found')=={'status':'PASS','http_status':404,'group_id':resource['id']}
                and d.get('admission',{}).get('status')=='LOCAL_EXECUTION_CLOSED'
                and bool(d.get('unrelated_before')) and d.get('unrelated_before')==d.get('unrelated_after')
                and all(d['storage_verification'].get(k)==0 for k in ('group_rows','participant_rows','session_rows','session_member_rows')))
        complete=complete and (cli.get('cli_binary_sha256')==binding['cli_binary_sha256']
            and len(cli.get('operations',[]))==11
            and all(op.get('cli_exit_code')==0 and op.get('credential_cleanup')=='CLEARED' for op in cli['operations'])
            and cli.get('context_receipt')==read(root/'workspace-fusion-context.json'))
    stopped=runtime.get('status')=='STOPPED' and type(runtime.get('returncode')) is int and runtime['returncode']==0
    cleared=flow.get('credentials',{}).get('credential_memory')=='CLEARED'
    status=('CANCELLED' if flow.get('status')=='CANCELLED' else
            'PASS' if complete and stopped and cleared and flow.get('status')=='LOCAL_WORKSPACE_INCIDENT_ROUNDTRIP_PASS' else
            'FAIL' if any(r.get('status')=='FAIL' for r in observed) else 'BLOCKED')
    requirements=[{'id':name,'status':next((r['status'] for r in observed if r.get('id')==name),'NOT_RUN')} for name in binding['report_inventory']['requirements']]
    visibility={'scope':'OWNED_LOCAL_FIXTURES','roles':'CEO,PM,ENG,QA','setup_visibility':'NOT_VERIFIED',
                'restoration':'NOT_PERFORMED','current_state_verification':'NOT_PERFORMED'}
    if ([(r.get('role'),r.get('retained')) for r in stages.get('api',{}).get('fixture_visibility',{}).get('actors',[])]
            ==[('CEO','public'),('PM','public'),('ENG','public')]
            and stages.get('cli',{}).get('qa_visibility',{}).get('status')=='ACKNOWLEDGED'):
        visibility['setup_visibility']='public'
    return {'schema_version':1,'case_id':CASE_ID,'version':1,'run_id':binding['run_id'],
        'status':status,'functional':'PASS' if complete else 'INCOMPLETE',
        'evidence_status':'PASS' if complete else 'INCOMPLETE','cleanup':'PASS' if status=='PASS' else 'BLOCKED',
        'assertions':observed,'requirements':requirements,'retained_visibility':visibility,
        'resource_disposition':flow.get('resource_disposition','UNVERIFIED'),
        'runtime_shutdown':runtime,'credentials':flow.get('credentials',{}),
        'full_release_status':'NOT_QUALIFIED','ready_to_execute':False}


def _summary(binding,case,issues):
    status=case.get('status','BLOCKED') if not issues else 'BLOCKED'
    return {'schema_version':1,'run_id':binding['run_id'],'status':'LOCAL_SUITE_PASS' if status=='PASS' else status,
        'target':{'tier':'local','environment_id':binding['run_id']},
        'scope':{'suite':SUITE,'selected_cases':[CASE_ID],'legacy_inventory_count': len(binding['legacy_inventory']),'full_release_status':'NOT_QUALIFIED',
                 'manifest_digest':fingerprint(binding)},'runner':{'executor_digest':binding['executor_digest']},
        'cases':[{'id':CASE_ID,'version':1,'status':status,'functional':case.get('functional','NOT_RUN'),
                  'evidence_status':case.get('evidence_status','INCOMPLETE'),'cleanup':case.get('cleanup','BLOCKED'),
                  'resource_disposition':case.get('resource_disposition','UNVERIFIED'),
                  'retained_visibility':case.get('retained_visibility',{})}],
        'requirements':case.get('requirements',[{'id':r,'status':'NOT_RUN'} for r in binding['report_inventory']['requirements']]),
        'reports':{'expected_assertion_count':17,'observed_assertion_count':len(case.get('assertions',[]))},
        'integrity':{'status':'PASS' if not issues else 'BLOCKED','issues':issues},'routes':[],
        'cleanup':{'verification_scope':'OWNED_INCIDENT_ONLY','physical_data_erasure':'NOT_VERIFIED',
                   'associated_history_purge':'NOT_PERFORMED'},
        'mock_boundary':{'not_proven':['remote gateway authentication','Bot engine reasoning','business Session terminality',
                                     'legacy run-context terminality','full release coverage']},
        'timing':{'interpretation':'functional diagnostic only; not a benchmark'},'ready_to_execute':False}


def publish_workspace(runtime,binding,flow):
    root=runtime.root
    _require(_bound(root)==binding and executor_digest()==binding['executor_digest'],'Workspace source changed before publication')
    _require(read(root/'workspace-incident-roundtrip-report.json')==flow and read(root/'owned-bcs.json')==runtime._record(),
             'Workspace publication must follow actual stopped-process result')
    pending=root/'workspace-publication-pending.json'
    pending_digest=_exclusive(pending,{'schema_version':1,'run_id':binding['run_id']})
    hashes={name:_sha(root/name) for name in _sources(root)}
    case=_project(root,binding)
    hashes[REPORT_NAME]=_exclusive(root/REPORT_NAME,case)
    summary=_summary(binding,case,[])
    from .reporting import write_reports
    outputs=write_reports(summary,root/'reports',{'all'})
    output_hashes={name:_sha(root/name) for name in OUTPUTS}
    completion={'schema_version':1,'contract':COMPLETION,'binding_digest':fingerprint(binding),
                'sources':hashes,'outputs':output_hashes}
    completion_digest=_exclusive(root/'workspace-completion.json',completion)
    _require(_sources(root)|{REPORT_NAME}==set(hashes) and all(_sha(root/name)==digest for name,digest in hashes.items())
             and all(_sha(root/name)==value for name,value in output_hashes.items())
             and _sha(pending)==pending_digest and _sha(root/'workspace-completion.json')==completion_digest,
             'Workspace sources changed during publication')
    pending.unlink()
    return {'status':summary['status'],'summary':summary,'outputs':outputs,'ready_to_execute':False}


def build_workspace_report(root:Path):
    _require(not root.is_symlink(),'Workspace root symlink is not admitted')
    root=root.resolve()
    binding=_bound(root)
    case={};issues=[]
    try:
        pending=root/'workspace-publication-pending.json'
        _require(not pending.exists() and not pending.is_symlink(),'Workspace publication is incomplete')
        completion=read(root/'workspace-completion.json')
        _require(completion.get('contract')==COMPLETION and completion.get('binding_digest')==fingerprint(binding),
                 'Workspace completion binding differs')
        _require(not (root/'reports').is_symlink(),'Workspace report directory cannot be a symlink')
        output_hashes=completion['outputs']
        _require(set(output_hashes)==set(OUTPUTS) and all(_sha(root/name)==value for name,value in output_hashes.items()),
                 'Workspace published report output changed')
        hashes=completion['sources']
        _require(set(hashes)==_sources(root)|{REPORT_NAME} and all(_sha(root/name)==value for name,value in hashes.items()),
                 'Workspace original source or Case changed')
        case=read(root/REPORT_NAME)
        _require(case==_project(root,binding),'Workspace Case differs from original execution evidence')
        _require(all(_sha(root/name)==value for name,value in hashes.items()) and read(root/'workspace-completion.json')==completion
                 and all(_sha(root/name)==value for name,value in output_hashes.items())
                 and not pending.exists() and not pending.is_symlink(),
                 'Workspace sources changed during offline projection')
    except (OSError,RunStateError,LedgerError,ValueError,KeyError,TypeError):
        issues.append('Workspace completion/source evidence is missing, invalid or changed')
    return _summary(binding,case,issues)
