"""Read-only dependency readiness and ambiguity-safe effect-center writes."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from clawweb_batch.adapters.odps import PyODPSData, CATE, JUDGE, FULL, literal
from clawweb_batch.artifacts import write_json
from clawweb_batch.core import API_PREFIX


def valid_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{8}', value):
        return False
    try:
        datetime.strptime(value, '%Y%m%d')
        return True
    except ValueError:
        return False


class ReadyData(PyODPSData):
    """Require all three legacy OpenClaw dependencies, never infer missing rows as zero."""
    def __init__(self, client, project, cutoff, cron_only=False):
        super().__init__(client, project, cron_only=cron_only)
        self.cutoff = cutoff
        self.readiness = {}
        self.count_cache = {}

    def daily_counts(self, start, end):
        end = min(end, self.cutoff)
        key = (start, end)
        if key in self.count_cache:
            return self.count_cache[key]
        rows = super().daily_counts(start, end)
        present = {}
        for table in (JUDGE, FULL):
            counts = self._read(f'SELECT dt,COUNT(*) row_count FROM {self.project}.{table} '
                                f'WHERE dt BETWEEN {literal(start)} AND {literal(end)} '
                                'GROUP BY dt ORDER BY dt DESC LIMIT 32')
            present[table] = {str(r['dt']) for r in counts
                              if valid_day(str(r['dt'])) and int(r['row_count']) > 0}
        common = present[JUDGE] & present[FULL]
        filtered = [r for r in rows if valid_day(str(r['dt'])) and r['dt'] in common]
        category_days = {str(r['dt']) for r in rows if valid_day(str(r['dt']))}
        self.readiness = {'cutoff': end, 'tables': [CATE, JUDGE, FULL],
                          'common_nonempty_dates': sorted(category_days & common),
                          'category_dates_missing_other_inputs': sorted(category_days - common),
                          'upstream_job_success_verified': False}
        self.count_cache[key] = filtered
        return filtered

    def validate_schema(self):
        required = {
            CATE: {'dt','is_cron','user_id','bot_id','task_complete_cate','weighted_cnt','raw_sampled_cnt'},
            JUDGE: {'dt','user_id','bot_id','session_id','start_time','end_time','sampling_group_key','llm_tasks_json','llm_judged_at'},
            FULL: {'dt','user_id','bot_id','session_id','file_path','bot_name','messages'},
        }
        for name, fields in required.items():
            schema = self.client.get_table(name, project=self.project).table_schema
            names = {c.name for c in list(schema.columns) + list(schema.partitions)}
            if fields - names:
                raise ValueError(f'dependency schema missing fields: {name}: {sorted(fields - names)}')
            # This release is the existing OpenClaw-only chain, not an engine-union migration.
            if 'engine' in names:
                raise ValueError(f'engine-aware schema requires reviewed engine-scoped joins: {name}')


class JournalCenter:
    """Prevent re-dispatch after an uncertain write, independently of HTTP idempotency."""
    def __init__(self, center, directory, *, create_only=False):
        self.center, self.directory = center, Path(directory)
        self.create_only = create_only

    def actions(self, owner, bot):
        return self.center.actions(owner, bot)

    def verification_candidates(self, lane, limit):
        return self.center.verification_candidates(lane, limit)

    def write(self, path, payload, key):
        if self.create_only and (path != API_PREFIX + '/actions' or payload.get('actionType') != 'ASSIGN_OWNER'):
            raise PermissionError('governance-only publication permits pending owner assignments only')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,220}', key):
            raise ValueError('invalid publication idempotency key')
        digest = hashlib.sha256(json.dumps([path,payload],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        record_path = self.directory / (key + '.json')
        if record_path.exists():
            record = json.loads(record_path.read_text())
            if record.get('digest') != digest:
                raise ValueError('idempotency payload changed; manual review required')
            if record.get('status') == 'confirmed':
                return record['receipt']
            raise ValueError('previous write has uncertain outcome; inspect before retrying')
        write_json(record_path, {'digest':digest, 'status':'dispatching'})
        receipt = self.center.write(path, payload, key)
        write_json(record_path, {'digest':digest, 'status':'confirmed', 'receipt':receipt})
        return receipt
