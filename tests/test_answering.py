import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from answering import generate, validate_answer, Reranker


class AnsweringTests(unittest.TestCase):
    def setUp(self):
        self.chunk = {'text': 'Reservations are required for this campground. Fees vary by site.',
                      'url': 'https://www.nps.gov/yose/planyourvisit/camping.htm',
                      'title': 'Camping', 'park': 'yose', 'ready_for_index': True,
                      'privacy_review_status': 'approved', 'temporal_review_status': 'approved',
                      'embedding_input': 'Camping: Reservations are required for this campground.'}
        self.contexts = [{'chunk_id': 'c1', 'chunk': self.chunk}]
        self.payload = {'status': 'answered', 'claims': [{'text': 'This campground requires reservations.',
                        'evidence': [{'chunk_id': 'c1', 'quote': 'Reservations are required for this campground.'}]}]}
        self.config = {'generation_model': 'gpt-4.1-mini-2025-04-14', 'maximum_output_tokens': 1600}
        self.result = {'query': 'Do I need a reservation?', 'contexts': self.contexts, 'index_mode': 'approved'}

    def test_citation_url_is_constructed_from_evidence(self):
        answer = validate_answer(self.payload, self.contexts)
        self.assertEqual(answer['status'], 'answered')
        self.assertIn(self.chunk['url'], answer['answer'])

    def test_missing_citation_refuses(self):
        self.payload['claims'][0]['evidence'] = []
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'missing_citation')

    def test_unknown_chunk_refuses(self):
        self.payload['claims'][0]['evidence'][0]['chunk_id'] = 'invented'
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'invalid_evidence')

    def test_invented_quote_refuses(self):
        self.payload['claims'][0]['evidence'][0]['quote'] = 'Camping costs $5.'
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'quote_not_in_source')

    def test_long_fee_quote_requires_multiple_short_evidence_spans(self):
        source = ('Each non-US resident aged 16 and older visiting Yellowstone National Park must pay '
                  'a $100 nonresident fee (in addition to the standard entrance fee), unless admitted '
                  'with an Annual or America the Beautiful Pass.')
        self.chunk['text'] = source
        self.payload['claims'][0]['text'] = 'Non-US residents aged 16 and older pay $100 extra unless admitted with an Annual or America the Beautiful Pass.'
        self.payload['claims'][0]['evidence'] = [{'chunk_id': 'c1', 'quote': source}]
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'invalid_evidence')
        self.payload['claims'][0]['evidence'] = [
            {'chunk_id': 'c1', 'quote': 'Each non-US resident aged 16 and older visiting Yellowstone National Park must pay a $100 nonresident fee'},
            {'chunk_id': 'c1', 'quote': 'unless admitted with an Annual or America the Beautiful Pass.'}]
        self.assertEqual(validate_answer(self.payload, self.contexts)['status'], 'answered')

    def test_model_links_refuse(self):
        self.payload['claims'][0]['text'] = 'See https://evil.example'
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'invalid_claim')

    def test_source_url_allowlist(self):
        self.chunk['url'] = 'https://www.nps.gov.evil.example/source'
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'invalid_source_url')

    def test_long_copy_across_claims_refuses(self):
        tokens = [f'word{i}' for i in range(30)]
        self.chunk['text'] = ' '.join(tokens)
        self.payload['claims'] = [
            {'text': ' '.join(tokens[:15]), 'evidence': [{'chunk_id': 'c1', 'quote': 'word0'}]},
            {'text': ' '.join(tokens[15:]), 'evidence': [{'chunk_id': 'c1', 'quote': 'word15'}]}]
        self.assertEqual(validate_answer(self.payload, self.contexts)['reason'], 'quotation_limit')

    def test_refusal_cannot_contain_claims(self):
        self.payload['status'] = 'refused'
        self.assertEqual(validate_answer(self.payload, self.contexts)['status'], 'refused')

    def test_pending_review_never_calls_api(self):
        for change in ['preview', 'privacy', 'temporal', 'ready']:
            result = copy.deepcopy(self.result)
            if change == 'preview':
                result['index_mode'] = 'development_preview'
            else:
                field = {'privacy': 'privacy_review_status', 'temporal': 'temporal_review_status', 'ready': 'ready_for_index'}[change]
                result['contexts'][0]['chunk'][field] = False if change == 'ready' else 'pending'
            transport = Mock()
            self.assertEqual(generate(result, self.config, transport)['reason'], 'corpus_review_pending')
            transport.assert_not_called()

    def test_empty_context_never_calls_api(self):
        self.result['contexts'] = []
        transport = Mock()
        self.assertEqual(generate(self.result, self.config, transport)['reason'], 'empty_context')
        transport.assert_not_called()

    def test_structured_request_and_completed_response(self):
        def transport(body):
            self.assertFalse(body['store'])
            self.assertTrue(body['text']['format']['strict'])
            self.assertEqual(body['model'], self.config['generation_model'])
            return {'status': 'completed', 'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': json.dumps(self.payload)}]}], 'usage': {'input_tokens': 100}}
        self.assertEqual(generate(self.result, self.config, transport)['status'], 'answered')

    def test_incomplete_output_refuses(self):
        transport = lambda _: {'status': 'incomplete', 'output': []}
        self.assertEqual(generate(self.result, self.config, transport)['reason'], 'incomplete_model_response')

    def test_model_refusal(self):
        transport = lambda _: {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal'}]}]}
        self.assertEqual(generate(self.result, self.config, transport)['reason'], 'model_refusal')

    def test_rerank_retains_scores_and_limits_context(self):
        ranker = Reranker.__new__(Reranker)
        ranker.config = {'maximum_pair_tokens': 512, 'maximum_context_chunks': 5,
                         'reranker_model': 'test', 'reranker_revision': 'test'}
        ranker.model = Mock()
        ranker.model.tokenizer.return_value = {'input_ids': [1, 2]}
        ranker.model.predict.return_value = [float(i) for i in range(20)]
        candidates = [{'rank': i + 1, 'chunk_id': str(i), 'chunk': self.chunk, 'scores': {'rrf_score': i}}
                      for i in range(20)]
        result = ranker.rerank({'query': 'question', 'candidates': candidates})
        self.assertEqual(len(result['reranked_candidates']), 20)
        self.assertEqual(len(result['contexts']), 5)
        self.assertEqual(result['contexts'][0]['pre_rank'], 20)
        self.assertEqual(result['contexts'][0]['scores']['rrf_score'], 19)

    def test_overlong_pair_is_rejected_before_prediction(self):
        ranker = Reranker.__new__(Reranker)
        ranker.config = {'maximum_pair_tokens': 512}
        ranker.model = Mock()
        ranker.model.tokenizer.return_value = {'input_ids': [1] * 513}
        with self.assertRaises(ValueError):
            ranker.rerank({'query': 'question', 'candidates': [{'rank': 1, 'chunk_id': 'c1', 'chunk': self.chunk}]})
        ranker.model.predict.assert_not_called()


if __name__ == '__main__':
    unittest.main()
