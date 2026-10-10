import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from chat_service import ChatEngine, request_query, friendly_error, safe_source_url
from streamlit.testing.v1 import AppTest


class ChatServiceTests(unittest.TestCase):
    def engine(self):
        engine = ChatEngine.__new__(ChatEngine)
        import threading
        engine.lock = threading.Lock()
        engine.retriever = Mock()
        engine.ranker = Mock()
        engine.answering = {}
        engine.ranker.rerank.return_value = {
            'query': 'question', 'index_mode': 'development_preview', 'contexts': [], 'candidates': [],
            'reranked_candidates': [], 'filters': {'park': 'yose', 'page_type': None, 'as_of': None},
            'candidate_count': 0, 'eligible_chunks': 20, 'candidate_pool_shortfall': True,
            'latency_seconds': .01, 'reranking_latency_seconds': 0,
            'temporal_note': 'Saved sources', 'warning': None}
        return engine

    def test_empty_result_is_explicit_refusal(self):
        result = self.engine().respond('What are Yosemite fees?')
        self.assertEqual(result['reason'], 'empty_context')
        self.assertEqual(result['status'], 'refused')

    def test_referral_bypasses_models(self):
        engine = self.engine()
        self.assertEqual(engine.respond('Someone is injured')['reason'], 'high_stakes')
        engine.retriever.search.assert_not_called()

    def test_followup_inherits_referral_but_not_assistant_claims(self):
        engine = self.engine()
        self.assertEqual(engine.respond('What should I do?', previous='Someone is injured')['reason'], 'high_stakes')
        self.assertIn('Earlier question: Yosemite campground fees', request_query('Are reservations required?', 'Yosemite campground fees'))

    def test_ambiguous_followup_requests_context(self):
        engine = self.engine()
        self.assertEqual(engine.respond('What about there?')['reason'], 'ambiguous_followup')
        engine.retriever.search.assert_not_called()

    def test_filter_and_followup_forwarding(self):
        engine = self.engine()
        engine.respond('What about reservations?', park='yose', page_type='pdf', as_of='2026-10-10', previous='Camping in Yosemite')
        args, kwargs = engine.retriever.search.call_args
        self.assertIn('Camping in Yosemite', args[0])
        self.assertEqual(kwargs, {'park': 'yose', 'page_type': 'pdf', 'as_of': '2026-10-10'})

    def test_cached_engine_reloads_current_provider(self):
        engine = self.engine()
        engine.answering = {'generation_provider': 'openai', 'generation_model': 'old-model'}
        engine.ranker.rerank.return_value['index_mode'] = 'approved'
        current = {'generation_provider': 'gemini', 'generation_model': 'current-model'}
        with patch('chat_service.generation_config', return_value=current), patch('chat_service.api_key', return_value='dummy-test-key'), patch('answering.generate', return_value={'status': 'refused', 'reason': 'empty_context', 'answer': 'No evidence'}) as generate:
            engine.respond('Camping information', cited=True)
            self.assertEqual(generate.call_args.args[1], current)
            self.assertEqual(engine.answering, current)

    def test_url_and_error_sanitization(self):
        self.assertFalse(safe_source_url('https://www.nps.gov.evil.test/source'))
        self.assertTrue(safe_source_url('https://www.nps.gov/yose/planyourvisit/camping.htm'))
        self.assertNotIn('secret', friendly_error(RuntimeError('secret request header')))


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.response = {'status': 'preview', 'message': 'Source preview only.', 'reason': None,
                         'sources': [{'title': 'Camping', 'url': 'https://www.nps.gov/yose/planyourvisit/camping.htm',
                                      'excerpt': 'Campground reservations', 'pages': []}], 'trace': None,
                         'filters': {'park': 'yose', 'page_type': None, 'as_of': None}}
        self.engine = Mock()
        self.engine.respond.return_value = self.response
        self.loader = patch('chat_service.ChatEngine', return_value=self.engine)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        import streamlit as st
        st.cache_resource.clear()
        self.app = AppTest.from_file(ROOT / 'app.py', default_timeout=15).run()

    def test_chat_history_followup_and_clear(self):
        self.assertFalse(self.app.exception)
        self.app.chat_input[0].set_value('Camping in Yosemite').run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.chat_message), 2)
        self.app.selectbox(key='followup').select('1. Camping in Yosemite').run()
        self.app.chat_input[0].set_value('Are reservations required?').run()
        self.assertEqual(self.engine.respond.call_args.kwargs['previous'], 'Camping in Yosemite')
        self.assertEqual(len(self.app.chat_message), 4)
        next(b for b in self.app.button if b.label == 'Clear conversation').click().run()
        self.assertEqual(len(self.app.chat_message), 0)
        self.assertEqual(self.app.session_state['messages'], [])

    def test_ready_app_defaults_to_cited_answer(self):
        with patch('generation_settings.api_key', return_value='dummy-test-key'), patch('chat_service.index_version', return_value='fixture-version'):
            app = AppTest.from_file(ROOT / 'app.py').run()
            self.assertEqual(app.radio(key='response_mode').value, 'Cited answer')
            app.chat_input[0].set_value('Camping information').run()
            self.assertTrue(self.engine.respond.call_args.kwargs['cited'])

    def test_preview_choice_is_preserved(self):
        with patch('generation_settings.api_key', return_value='dummy-test-key'), patch('chat_service.index_version', return_value='fixture-version'):
            app = AppTest.from_file(ROOT / 'app.py').run()
            app.radio(key='response_mode').set_value('Source preview').run()
            app.chat_input[0].set_value('Camping information').run()
            self.assertEqual(app.radio(key='response_mode').value, 'Source preview')
            self.assertFalse(self.engine.respond.call_args.kwargs['cited'])

    def test_adding_key_switches_existing_preview_session(self):
        with patch('chat_service.index_version', return_value='fixture-version'):
            with patch('generation_settings.api_key', return_value=''):
                app = AppTest.from_file(ROOT / 'app.py').run()
                self.assertEqual(app.radio(key='response_mode').value, 'Source preview')
            with patch('generation_settings.api_key', return_value='dummy-test-key'):
                app.run()
                self.assertEqual(app.radio(key='response_mode').value, 'Cited answer')

    def test_filters_and_cited_mode_are_forwarded(self):
        self.app.selectbox(key='park').select('Yosemite').run()
        self.app.selectbox(key='source_type').select('PDF guides').run()
        self.app.checkbox(key='dated').check().run()
        self.app.radio(key='response_mode').set_value('Cited answer').run()
        self.app.chat_input[0].set_value('Camping information').run()
        self.assertFalse(self.app.exception)
        values = self.engine.respond.call_args.kwargs
        self.assertEqual(values['park'], 'yose')
        self.assertEqual(values['page_type'], 'pdf')
        self.assertIsNotNone(values['as_of'])
        self.assertTrue(values['cited'])

    def test_error_keeps_conversation_without_leaking_details(self):
        self.engine.respond.side_effect = RuntimeError('secret key and internal path')
        self.app.chat_input[0].set_value('Camping information').run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.chat_message), 2)
        self.assertTrue(self.app.error)
        self.assertNotIn('secret', self.app.error[0].value)

    def test_quota_message_is_displayed_exactly(self):
        from urllib.error import HTTPError
        self.engine.respond.side_effect = HTTPError('https://example.test', 429, 'private detail', {}, None)
        self.app.chat_input[0].set_value('Camping information').run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.error[0].value, 'Free quota reached—please try again later.')

    def test_sessions_have_separate_history(self):
        self.app.chat_input[0].set_value('Camping information').run()
        other = AppTest.from_file(ROOT / 'app.py').run()
        self.assertEqual(len(other.chat_message), 0)
        self.assertEqual(len(self.app.chat_message), 2)

    def test_cited_answer_and_ranking_trace_render(self):
        response = copy.deepcopy(self.response)
        response['status'] = 'answered'
        response['message'] = 'Reservations are required. [NPS source](https://www.nps.gov/yose/planyourvisit/camping.htm)'
        response['trace'] = {
            'candidate_count': 1, 'eligible_chunks': 1, 'candidate_pool_shortfall': True,
            'latency_seconds': .01, 'reranking_latency_seconds': .02,
            'generation': {'generation_latency_seconds': .1, 'usage': {'input_tokens': 10}},
            'rankings': [{'title': 'Camping', 'pre_rank': 1, 'post_rank': 1,
                          'reranker_score': 2, 'selected': True}],
            'temporal_note': 'Source updates are not live conditions.', 'warning': None}
        self.engine.respond.return_value = response
        self.app.chat_input[0].set_value('Camping reservations').run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any('NPS source' in m.value for m in self.app.markdown))
        self.assertEqual(len(self.app.dataframe), 1)
        self.assertEqual(len(self.app.metric), 3)


if __name__ == '__main__':
    unittest.main()
