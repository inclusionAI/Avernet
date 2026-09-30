"""Read-only PyODPS adapter. No SQL supplied by an LLM or CLI user."""
from __future__ import annotations

import re

CATE = "dws_sec_teamclaw_arca_task_complete_cate_di"
JUDGE = "dws_sec_log_teamclaw_arca_openclaw_judge_result_di"
FULL = "dws_sec_log_teamclaw_arca_openclaw_session_full_di"


def literal(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[\w:.-]{1,160}", value):
        raise ValueError("invalid query identity/date")
    return "'" + value + "'"


class PyODPSData:
    def __init__(self, client, project: str):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", project):
            raise ValueError("invalid project")
        self.client, self.project = client, project

    def _read(self, sql: str) -> list[dict]:
        if not sql.lstrip().upper().startswith(("SELECT ", "WITH ")):
            raise ValueError("read-only query adapter")
        with self.client.execute_sql(sql).open_reader() as reader:
            return [{c.name: row[c.name] for c in reader.schema} for row in reader]

    def daily_counts(self, start: str, end: str) -> list[dict]:
        return self._read(f"SELECT dt,is_cron,SUM(weighted_cnt) weighted_cnt,SUM(raw_sampled_cnt) raw_sampled_cnt "
                          f"FROM {self.project}.{CATE} WHERE dt BETWEEN {literal(start)} AND {literal(end)} "
                          "GROUP BY dt,is_cron ORDER BY dt DESC,is_cron LIMIT 64")

    def ranking(self, start: str, end: str) -> list[dict]:
        rows = self._read(f"SELECT user_id,bot_id,is_cron,task_complete_cate,SUM(weighted_cnt) weighted_cnt,"
                          f"SUM(raw_sampled_cnt) raw_sampled_cnt FROM {self.project}.{CATE} "
                          f"WHERE dt BETWEEN {literal(start)} AND {literal(end)} "
                          "GROUP BY user_id,bot_id,is_cron,task_complete_cate LIMIT 100001")
        if len(rows) > 100000:
            raise ValueError("ranking exceeds complete-read bound")
        return rows

    def sessions(self, owner: str, bot: str, start: str, end: str, limit: int) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("invalid session bound")
        # Deduplicate daily snapshots, retaining the newest complete source record.
        return self._read(f"""WITH candidates AS (
 SELECT j.user_id,j.bot_id,j.session_id,j.dt,j.start_time,j.end_time,j.sampling_group_key,j.llm_tasks_json,
 s.file_path,s.bot_name,s.messages,
 COUNT(*) OVER(PARTITION BY j.user_id,j.bot_id,j.session_id,j.dt) source_rows,
 ROW_NUMBER() OVER(PARTITION BY j.user_id,j.bot_id,j.session_id ORDER BY j.dt DESC,j.llm_judged_at DESC) rn
 FROM {self.project}.{JUDGE} j
 LEFT JOIN {self.project}.{FULL} s ON j.user_id=s.user_id AND j.bot_id=s.bot_id
 AND j.session_id=s.session_id AND j.dt=s.dt
 AND s.dt BETWEEN {literal(start)} AND {literal(end)}
 WHERE j.dt BETWEEN {literal(start)} AND {literal(end)}
 AND j.user_id={literal(owner)} AND j.bot_id={literal(bot)}
 ) SELECT user_id,bot_id,session_id,dt,start_time,end_time,sampling_group_key,llm_tasks_json,file_path,bot_name,messages,source_rows
 FROM candidates WHERE rn=1 ORDER BY end_time DESC,session_id LIMIT {limit + 1}""")
