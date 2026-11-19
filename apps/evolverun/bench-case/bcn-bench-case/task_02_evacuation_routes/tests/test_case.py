"""Verify the Case/Judge contract and reject manager-invented member votes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

BENCH = Path(__file__).resolve().parents[1]
REPO = next(p for p in BENCH.parents if (p / 'apps/bcs').is_dir())
sys.path.insert(0, str(REPO / 'apps/evolverun/clawweb-skills/clawevolve-skills/clawbench-bcn/scripts'))
from core.profile_loader import load_bench
from core.grading import code_grade

ALIASES = ['manager', 'member_b', 'member_c', 'member_d']


def vote(choice='West City'):
    return json.dumps({'phase': 'final', 'choice': choice, 'rationale': 'Based on the exchanged route facts.'})


def fixture():
    members = {alias: 'bot-' + alias for alias in ALIASES}
    names = {alias: 'display-' + alias for alias in ALIASES}
    output = vote()
    messages = []
    for index, alias in enumerate(ALIASES[1:]):
        messages.append({'sender': names['manager'], 'role': 'tool_result', 'timestamp': 10 + index,
                         'metadata': {'tool_name': 'bcs_assign_task', 'is_error': False,
                                      'arguments': {'target_bot': names[alias]},
                                      'result': json.dumps({'ok': True, 'task_id': 'task-' + alias})}})
        messages.append({'sender': names[alias], 'role': 'assistant', 'timestamp': 20 + index,
                         'run_id': 'task-' + alias, 'content': vote()})
    messages.append({'sender': names['manager'], 'role': 'tool_result', 'timestamp': 100,
                     'metadata': {'tool_name': 'bcs_task_complete', 'is_error': False,
                                  'arguments': {'summary': output}, 'result': '{"ok":true}'}})
    return {'run': {'members': members, 'member_names': names, 'entry_member': 'manager'},
            'result': {'group_messages_complete': True, 'evidence_complete': True, 'evidence_stable': True},
            'session': {'status': 'completed', 'output': output}, 'messages': messages[::-1]}


class HiddenBenchCaseTests(unittest.TestCase):
    def score(self, evidence):
        case, _, _ = load_bench(BENCH)
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'bcn-evidence.json').write_text(json.dumps(evidence))
            return code_grade(case, directory, 10, [])['post_average_accuracy']

    def test_asset_contract_and_fact_distribution(self):
        case, source, manifest = load_bench(BENCH)
        original = json.loads((BENCH / 'source/hiddenbench-task-1.json').read_text())
        self.assertEqual(case['meta']['id'], BENCH.name)
        self.assertEqual(case['meta']['grading_type'], 'automated')
        self.assertEqual(manifest['entry_member'], 'manager')
        self.assertEqual(len(manifest['bots']), 4)
        for i, bot in enumerate(manifest['bots']):
            own = source / bot['source']
            knowledge = (own / 'KNOWLEDGE.md').read_text()
            for fact in original['shared_information']:
                self.assertIn(fact, knowledge)
            for j, fact in enumerate(original['hidden_information']):
                self.assertEqual(fact in knowledge, i == j)
            for path in own.rglob('*'):
                if path.is_file():
                    text = path.read_text()
                    self.assertNotIn('correct_answer', text)
                    self.assertNotIn('hidden_information', text)
                    self.assertNotIn('source/hiddenbench-task', text)
        self.assertEqual(self.score(fixture()), 1.0)

    def test_fraction_is_not_all_or_nothing(self):
        for wrong in range(1, 4):
            evidence = fixture()
            replies = [m for m in evidence['messages'] if m['role'] == 'assistant']
            for reply in replies[:wrong]:
                reply['content'] = vote('East Town')
            self.assertEqual(self.score(evidence), (4 - wrong) / 4)

    def test_manager_vote_is_formal_output_not_relayed_votes(self):
        evidence = fixture()
        evidence['session']['output'] = vote('North Hill')
        evidence['messages'][0]['metadata']['arguments']['summary'] = evidence['session']['output']
        self.assertEqual(self.score(evidence), .75)
        evidence['messages'] = [m for m in evidence['messages'] if m['role'] != 'assistant']
        evidence['messages'].append({'sender': 'display-manager', 'role': 'assistant', 'timestamp': 80,
                                     'content': 'All three workers chose West City.'})
        self.assertEqual(self.score(evidence), 0)

    def test_final_vote_requires_real_sender_task_and_time(self):
        for change in ('sender', 'task', 'after', 'before', 'failed_dispatch', 'undispatched'):
            with self.subTest(change=change):
                evidence = fixture()
                reply = next(m for m in evidence['messages'] if m.get('sender') == 'display-member_b')
                dispatch = next(m for m in evidence['messages']
                                if m.get('metadata', {}).get('arguments', {}).get('target_bot') == 'display-member_b')
                if change == 'sender': reply['sender'] = 'foreign-bot'
                elif change == 'task': reply['run_id'] = 'unrelated-task'
                elif change == 'after': reply['timestamp'] = 101
                elif change == 'before': reply['timestamp'] = 1
                elif change == 'failed_dispatch': dispatch['metadata']['result'] = '{"ok":false}'
                else: evidence['messages'].remove(dispatch)
                self.assertEqual(self.score(evidence), .75)

    def test_last_reply_wins_instead_of_an_earlier_correct_answer(self):
        evidence = fixture()
        reply = next(m for m in evidence['messages'] if m.get('sender') == 'display-member_b')
        later = copy.deepcopy(reply)
        later.update(timestamp=90, content=vote('North Hill'))
        evidence['messages'].append(later)
        self.assertEqual(self.score(evidence), .75)

    def test_invalid_reply_does_not_pass_keyword_matching(self):
        for content in ('West City', 'Example: ' + vote(), '{bad json',
                        json.dumps({'phase': 'draft', 'choice': 'West City', 'rationale': 'Draft'}),
                        json.dumps({'phase': 'final', 'choice': 'West City', 'rationale': ''}),
                        json.dumps({'votes': {alias: 'West City' for alias in ALIASES}})):
            with self.subTest(content=content):
                evidence = fixture()
                next(m for m in evidence['messages'] if m.get('sender') == 'display-member_b')['content'] = content
                self.assertEqual(self.score(evidence), .75)

    def test_missing_evidence_is_an_error_not_zero_or_pass(self):
        for field in ('group_messages_complete', 'evidence_complete', 'evidence_stable'):
            evidence = fixture(); evidence['result'][field] = False
            with self.subTest(field=field), self.assertRaises(ValueError): self.score(evidence)
        for change in ('completion', 'summary', 'team', 'timestamp'):
            evidence = fixture()
            if change == 'completion': evidence['messages'].pop(0)
            elif change == 'summary': evidence['messages'][0]['metadata']['arguments']['summary'] = 'different output'
            elif change == 'team': evidence['run']['members'].pop('member_d')
            else: evidence['messages'][0]['timestamp'] = 'not a BCS timestamp'
            with self.subTest(change=change), self.assertRaises(ValueError): self.score(evidence)


if __name__ == '__main__':
    unittest.main()
