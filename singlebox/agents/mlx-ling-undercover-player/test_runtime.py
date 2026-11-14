"""Boundary regressions for reasoning, vote validity, and model requests."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rules import _strip_tags, clamp_clue, parse_vote, ABSTAIN
from player import GamePlayer
from main import make_infer_fn


class RuntimeTests(unittest.TestCase):
    def test_reasoning_vote_is_not_the_answer(self):
        self.assertEqual(parse_vote('<think>可能我投1号</think>我投3号'), '我投3号')
        self.assertEqual(clamp_clue('分析不应出现</think>甜脆多汁'), '甜脆多汁')

    def test_unfinished_thinking_is_not_exposed(self):
        self.assertEqual(parse_vote('<think>我投1号'), ABSTAIN)
        self.assertEqual(_strip_tags('<think>秘密分析'), '')

    def test_votes_must_be_legal(self):
        for text in ['我投2号', '我投8号', '解释我投3号', '我投3号\n解释']:
            self.assertEqual(parse_vote(text, own_seat=2, allowed_seats=[1, 2, 4]), ABSTAIN)
        self.assertEqual(parse_vote('我投4号', own_seat=2, allowed_seats=[1, 2, 4]), '我投4号')

    def test_empty_speech_fallback_respects_banned_characters(self):
        text = clamp_clue('苹果', banned_chars=set('苹果和大'))
        self.assertTrue(text)
        self.assertFalse(set(text) & set('苹果和大'))
        self.assertLessEqual(len(text), 25)

    def test_pk_fallback_never_self_votes(self):
        task = '''[State Machine Task]
node_id: vote_2
[Input]
{"alive": [2, 3], "history": []}
[Instruction]
你是2号，你的词是【苹果】。第2轮PK投票。可以投：2号、3号；'''
        player = GamePlayer(lambda prompt: '我投2号')
        self.assertEqual(asyncio.run(player.on_task('local', task)), '我投3号')

    def test_regular_vote_cannot_target_dead_player(self):
        task = '''[State Machine Task]
node_id: vote_2
[Input]
{"alive": [2, 3], "history": []}
[Instruction]
你是2号，你的词是【苹果】。第2轮投票。'''
        self.assertEqual(asyncio.run(GamePlayer(lambda p: '我投1号').on_task('local', task)), ABSTAIN)

    def test_request_selects_adapter_and_default_thinking(self):
        with patch('openai.OpenAI') as sdk:
            client = sdk.return_value
            client.chat.completions.create.return_value = SimpleNamespace(choices=[
                SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='我投3号'))])
            infer = make_infer_fn('http://127.0.0.1:1/v1', adapter_path='/local/adapter')
            self.assertEqual(infer('prompt'), '我投3号')
            kwargs = client.chat.completions.create.call_args.kwargs
            self.assertEqual(kwargs['extra_body']['adapters'], '/local/adapter')
            self.assertTrue(kwargs['extra_body']['chat_template_kwargs']['enable_thinking'])
            self.assertEqual(kwargs['temperature'], 0.7)
            self.assertEqual(sdk.call_args.kwargs['max_retries'], 0)

    def test_partial_answer_is_not_accepted(self):
        with patch('openai.OpenAI') as sdk:
            client = sdk.return_value
            client.chat.completions.create.side_effect = [
                SimpleNamespace(choices=[SimpleNamespace(finish_reason='length', message=SimpleNamespace(content='我投'))]),
                SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='我弃权'))])]
            self.assertEqual(make_infer_fn('http://127.0.0.1:1/v1')('prompt'), '我弃权')
            self.assertEqual(client.chat.completions.create.call_count, 2)


    def test_cache_namespace_separates_model_modes(self):
        import mlx.core as mx
        import serve
        from mlx_lm.models.cache import KVCache
        cache = KVCache()
        cache.update_and_fetch(mx.ones((1, 1, 2, 1)), mx.ones((1, 1, 2, 1)))
        store = serve.RoutedPromptCache(2)
        serve._ROUTE.adapter = False
        store.insert_cache(('same-model',), [1, 2], [cache])
        self.assertIsNotNone(store.fetch_nearest_cache(('same-model',), [1, 2])[0])
        serve._ROUTE.adapter = True
        self.assertIsNone(store.fetch_nearest_cache(('same-model',), [1, 2])[0])

    def test_all_lora_module_kinds_restore_scales(self):
        import serve
        from mlx_lm.tuner.lora import LoRALinear, LoRASwitchLinear, LoRAEmbedding
        modules = [LoRALinear(2, 2, r=1), LoRASwitchLinear(2, 2, 2, r=1), LoRAEmbedding(3, 2, r=1)]
        model = SimpleNamespace(modules=lambda: modules)
        serve._set_lora_mode(model, False)
        self.assertEqual([m.scale for m in modules], [0, 0, 0])
        serve._set_lora_mode(model, True)
        self.assertEqual([m.scale for m in modules], [20, 20, 20])


    def test_ambiguous_base_and_adapter_configuration_is_rejected(self):
        import subprocess
        import sys
        from pathlib import Path
        process = subprocess.run([sys.executable, str(Path(__file__).with_name('main.py')),
                                  '--base-model', '--adapter-path', '/local/adapter'],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn('not both', process.stderr)


if __name__ == '__main__':
    unittest.main()
