"""Complete local execution adapter; core never inspects processes or paths."""
from .agents import LocalTeam
from .services import LocalServices
from .bcn import BcnAdapter
from .evidence import collect_member, archived_tool_calls
from core.transcript import build_transcript


class LocalRuntime:
    def __init__(self, config, source, output):
        self.config, self.source, self.output = config, source, output
        self.services = LocalServices(config, output.directory)
        self.team = self.client = None
        self.gid = self.sid = None
        self.session = {}
        self.metadata = {'mode': 'local', 'model_id': config.get('model_id'),
                         'answer_isolation': 'Prompt only; same-host filesystem is not a security boundary'}
        self.stopped = False
        self.group_terminated = None

    def prepare(self):
        self.services.start(self.output.progress)
        self.team = LocalTeam(self.config, self.source, self.output.directory)
        self.metadata.update(profile_sha256=self.team.digest, member_profiles=self.team.mapping,
                             member_names=self.team.display_names, agent_ports=self.team.ports)
        self.output.progress('启动本次被测团队')
        self.team.command('start')
        self.metadata['members'] = self.team.members()
        self.client = BcnAdapter(self.config['repository'] / 'apps/bcs/target/debug/bcs-cli',
                                 self.config['bcs_url'], self.team.bcn_env(), self.output.directory)

    def submit(self, prompt, entry_member):
        self.output.progress('被测团队已启动；建群并一次性提交协作目标')
        self.metadata['submission_status'] = 'uncertain'
        self.gid, self.sid = self.client.create(self.metadata['members'], prompt,
                                               self.output.run_id, entry_member)
        self.metadata.update(group_id=self.gid, session_id=self.sid, entry_member=entry_member, submission_status='confirmed')
        return {'group_id': self.gid, 'session_id': self.sid}

    def poll(self, timeout):
        import subprocess
        try:
            self.session = self.client.get(self.sid, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise TimeoutError('BCS status query timed out') from None
        state = self.session
        status = state.get('status')
        if status not in ('running', 'completed'):
            status = 'failed'
        output = state.get('output')
        if status == 'completed' and (state.get('error_message') or not isinstance(output, str) or not output.strip()):
            status = 'failed'
        return {'status': status, 'output': output if status == 'completed' else '', 'session': state}

    def quiesce(self, reason):
        if self.team and not self.stopped:
            try:
                self.team.command('stop')
                self.team.verify_stopped()
                self.stopped = True
                self.output.progress('本次Bot已停止，端口退出检查通过')
            except Exception:
                self.output.progress('警告：本次Bot停止未确认，证据不视为稳定')
            finally:
                self.metadata['agent_processes'] = [p.pid for p in self.team.processes.children]
        if self.client and self.gid and not self.group_terminated:
            try:
                self.client.call('terminate-group', '--group', self.gid, as_json=False)
                self.group_terminated = True
            except Exception:
                self.group_terminated = False
        return {'evidence_stable': bool(self.stopped and self.group_terminated),
                'bots_stopped': self.stopped, 'group_terminated': self.group_terminated}

    def collect(self):
        if not self.sid:
            return {'messages': [], 'session': self.session, 'summary': {'evidence_complete': False}}
        self.output.progress('回收群消息、成员会话和模型用量')
        messages, complete = [], False
        try:
            messages = self.client.call('session', 'messages', self.sid, '--limit', '1000')
            complete = isinstance(messages, list) and len(messages) < 1000
            self.output.write_json('evidence/group-messages.json', messages)
        except Exception:
            pass
        per_member, tool_events = {}, []
        for alias, profile in self.team.mapping.items():
            try:
                item = collect_member(self.team.root / 'profiles' / profile, self.sid, self.output.evidence / alias)
            except Exception:
                item = {'evidence_complete': False, 'total_tokens': None, 'missing': ['collection failed']}
            try:
                tool_events.extend(archived_tool_calls(item.get('files', []), self.metadata['members'][alias], self.sid))
            except (OSError, ValueError):
                item['evidence_complete'] = False
                item.setdefault('missing', []).append('tool call evidence could not be read')
            per_member[alias] = item
            self.output.progress(f"成员 {alias}: 日志完整={item['evidence_complete']}, 模型用量={item['total_tokens']}")
        def total(key):
            values = [v.get(key) for v in per_member.values()]
            return sum(values) if values and all(v is not None for v in values) else None
        return {'messages': messages, 'session': self.session,
                'transcript': build_transcript(messages + tool_events, self.sid), 'summary': {
            'evidence_origin': 'real', 'group_messages_complete': complete,
            'member_evidence': per_member, 'evidence_complete': complete and bool(per_member) and all(v['evidence_complete'] for v in per_member.values()),
            'usage_complete': bool(per_member) and all(v.get('usage_complete', False) for v in per_member.values()),
            'total_tokens': total('total_tokens'), 'tool_calls': total('tool_calls')}}

    def release(self):
        # Always attempt service cleanup even if an Agent failed to stop.
        details = {}
        try:
            if self.client and self.gid and not self.group_terminated:
                try:
                    self.client.call('terminate-group', '--group', self.gid, as_json=False)
                    self.group_terminated = True
                except Exception:
                    self.group_terminated = False
            if self.team and not self.stopped:
                self.team.command('stop'); self.team.verify_stopped(); self.stopped = True
        finally:
            self.services.stop()
        details.update(bots_stopped=self.stopped if self.team else True, services_cleaned=True,
                       group_terminated=self.group_terminated)
        details['resources_released'] = (details['bots_stopped'] and (not self.gid or bool(self.group_terminated))
                                         and self.metadata.get('submission_status') != 'uncertain')
        return details
