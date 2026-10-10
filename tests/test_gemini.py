import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from answering import generate, QuotaReached
from generation_settings import api_key, QUOTA_MESSAGE
from chat_service import friendly_error


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.config = {'generation_provider': 'gemini', 'generation_model': 'gemini-3.5-flash-lite',
                       'maximum_output_tokens': 1600}
        chunk = {'text': 'Reservations are required.', 'title': 'Camping', 'park': 'yose',
                 'url': 'https://www.nps.gov/yose/planyourvisit/camping.htm', 'ready_for_index': True,
                 'privacy_review_status': 'approved', 'temporal_review_status': 'approved'}
        self.result = {'query': 'Are reservations needed?', 'index_mode': 'approved',
                       'contexts': [{'chunk_id': 'c1', 'chunk': chunk}]}
        self.payload = {'status': 'answered', 'claims': [{'text': 'You need a reservation.',
                        'evidence': [{'chunk_id': 'c1', 'quote': 'Reservations are required.'}]}]}

    def response(self, body):
        self.assertEqual(body['generationConfig']['responseFormat']['text']['mimeType'], 'APPLICATION_JSON')
        self.assertEqual(body['generationConfig']['candidateCount'], 1)
        self.assertIn('only', body['systemInstruction']['parts'][0]['text'])
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(self.payload)}]}}],
                'usageMetadata': {'promptTokenCount': 100}, 'modelVersion': 'test-version'}

    def test_gemini_response_uses_same_citation_validator(self):
        answer = generate(self.result, self.config, self.response)
        self.assertEqual(answer['status'], 'answered')
        self.assertIn('https://www.nps.gov/', answer['answer'])
        self.assertEqual(answer['provider'], 'gemini')
        self.assertEqual(answer['model_version'], 'test-version')

    def test_fabricated_gemini_evidence_refuses(self):
        self.payload['claims'][0]['evidence'][0]['chunk_id'] = 'invented'
        self.assertEqual(generate(self.result, self.config, self.response)['reason'], 'invalid_evidence')

    def test_gemini_incomplete_and_blocked_fail_closed(self):
        incomplete = lambda _: {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': '{}'}]}}]}
        self.assertEqual(generate(self.result, self.config, incomplete)['reason'], 'incomplete_model_response')
        blocked = lambda _: {'promptFeedback': {'blockReason': 'SAFETY'}}
        self.assertEqual(generate(self.result, self.config, blocked)['reason'], 'model_refusal')

    def test_http429_has_exact_message_and_no_retry(self):
        error = HTTPError('https://generativelanguage.googleapis.com/', 429, 'sensitive provider detail', {}, None)
        with patch('answering.api_key', return_value='dummy-test-key'), patch('answering.urlopen', side_effect=error) as network:
            with self.assertRaises(QuotaReached) as raised:
                generate(self.result, self.config)
            self.assertEqual(str(raised.exception), QUOTA_MESSAGE)
            self.assertEqual(network.call_count, 1)
            request = network.call_args.args[0]
            self.assertNotIn('dummy-test-key', request.full_url)
            self.assertIn('generativelanguage.googleapis.com', request.full_url)
        self.assertEqual(friendly_error(error), QUOTA_MESSAGE)

    def test_resource_exhausted_response_has_quota_message(self):
        with self.assertRaises(QuotaReached):
            generate(self.result, self.config, lambda _: {'error': {'status': 'RESOURCE_EXHAUSTED', 'code': 429}})

    def test_env_reads_file_and_respects_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {}, clear=True):
            root = Path(directory)
            (root / '.env').write_text('GEMINI_API_KEY=dummy-local-key\n')
            self.assertEqual(api_key(self.config, root), 'dummy-local-key')
            with patch.dict('os.environ', {'GEMINI_API_KEY': 'dummy-environment-key'}):
                self.assertEqual(api_key(self.config, root), 'dummy-environment-key')
            (root / '.env').write_text('GEMINI_API_KEY=\n')
            self.assertEqual(api_key(self.config, root), '')


if __name__ == '__main__':
    unittest.main()
