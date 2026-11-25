"""Independent evaluator handoff; pure validation, no transport or persistence."""
import copy
import math
import time
from .grading import checked_scores, combine


def evaluation_request(case):
    return {"case": {"meta": case["meta"], "sections": case["sections"]},
            "result": "result.json", "evidence": "evidence/", "final": "artifacts/final.md",
            "transcript": "artifacts/transcript.json", "group_evidence": "artifacts/bcn-evidence.json",
            "report_output": "analysis-report.md",
            "instructions": "独立评测 Agent 阅读证据生成报告；不得加入群、改变执行终态或代码分。超时也需分析；缺失证据明确披露。"}


def finalize(request, original, report, assessment=None):
    if not isinstance(report, str) or not report.strip():
        raise ValueError('Evaluation report must not be empty')
    assessment = assessment if assessment is not None else {}
    if not isinstance(assessment, dict):
        raise ValueError('Assessment must be an object')
    result = copy.deepcopy(original)
    meta = request['case']['meta']
    if result['grading_status'] == 'awaiting_evaluation_agent':
        if result['execution_status'] != 'completed' or not result.get('evidence_stable') or not result.get('evidence_complete'):
            raise ValueError('Cannot score an unfinished or unclean run')
        scores = checked_scores(assessment.get('scores'))
        total = assessment.get('total')
        if type(total) not in (int, float) or not math.isfinite(total) or not 0 <= total <= 1:
            raise ValueError('Model Judge total must be a finite number in [0,1]')
        if meta['grading_type'] == 'hybrid':
            result['score'] = combine(result['breakdown']['automated'], total, meta['grading_weights'])
        elif meta['grading_type'] == 'llm_judge':
            result['score'] = total
        else:
            raise ValueError('This grading type does not accept model scores')
        result['breakdown']['llm_judge'] = scores
        result['llm_judge_total'] = total
        result['grading_status'] = 'completed'
        result['notes'] = str(assessment.get('notes', ''))
    elif 'scores' in assessment or 'total' in assessment:
        raise ValueError('Scores not accepted: this run does not await model grading')
    result['report_status'] = 'completed'
    result['evaluation_finished_at'] = time.time()
    # Null means the independent host did not supply its usage; never add it to team usage.
    result['evaluation_agent_usage'] = assessment.get('usage')
    result['evaluation_evidence_notes'] = assessment.get('evidence_notes', 'Host did not declare evidence coverage')
    return result
