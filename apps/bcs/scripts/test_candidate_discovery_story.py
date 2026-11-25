"""Guard the live discovery story's assertions against vacuous/incorrect results."""
import copy
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parent / 'e2e-test/oauth/candidate_discovery.py'
SPEC = importlib.util.spec_from_file_location('candidate_discovery', PATH)
STORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORY)


class CandidateAssertions(unittest.TestCase):
    def setUp(self):
        self.result = {'search_mode': 'semantic', 'items': [
            {'bot': {'bot_id': bot}, 'is_friend': bot == 'friend', 'score': 0.9,
             'short_profile': 'candidate profile', 'tags': {'specialty': 'planning'}}
            for bot in ('human-only', 'friend')]}

    def test_semantic_order_and_friend_markers(self):
        STORY.assert_hits(self.result, 'semantic', ['human-only', 'friend'], ['friend'])
        for mutation in ('reverse', 'empty', 'friend', 'score', 'mode'):
            result = copy.deepcopy(self.result)
            if mutation == 'reverse':
                result['items'].reverse()
            elif mutation == 'empty':
                result['items'] = []
            elif mutation == 'friend':
                result['items'][1]['is_friend'] = False
            elif mutation == 'score':
                result['items'][0]['score'] = None
            else:
                result['search_mode'] = 'name_fallback'
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                STORY.assert_hits(result, 'semantic', ['human-only', 'friend'], ['friend'])

    def test_fallback_never_claims_a_semantic_score(self):
        result = {'search_mode': 'name_fallback', 'items': [
            {'bot': {'bot_id': 'name-match'}, 'is_friend': False, 'tags': {}}]}
        STORY.assert_hits(result, 'name_fallback', ['name-match'])
        result['items'][0]['score'] = 0
        with self.assertRaises(AssertionError):
            STORY.assert_hits(result, 'name_fallback', ['name-match'])


if __name__ == '__main__':
    unittest.main()
