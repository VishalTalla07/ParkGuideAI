import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluation import evidence_mapping, retrieval_metrics, aggregate


class EvaluationTests(unittest.TestCase):
    def test_duplicate_chunks_do_not_increase_evidence_recall_or_dcg(self):
        mapping = {'a': ['one', 'duplicate'], 'b': ['two']}
        result = retrieval_metrics(['one', 'duplicate', 'two'], mapping, {'a': 'page1', 'b': 'page2'})
        self.assertEqual(result['recall_at_5'], 1)
        self.assertEqual(result['mrr_at_20'], 1)
        self.assertLess(result['ndcg_at_10'], 1)
        self.assertTrue(result['all_required_sources_at_5'])

    def test_partial_multi_hop_coverage_is_not_full_source_coverage(self):
        result = retrieval_metrics(['one'], {'a': ['one'], 'b': ['two']}, {'a': 'page1', 'b': 'page2'})
        self.assertEqual(result['recall_at_5'], .5)
        self.assertFalse(result['all_required_sources_at_5'])

    def test_multiple_units_in_one_chunk_have_achievable_binary_ideal(self):
        result = retrieval_metrics(['both'], {'a': ['both'], 'b': ['both']}, {'a': 'page', 'b': 'page'})
        self.assertEqual(result['recall_at_5'], 1)
        self.assertEqual(result['ndcg_at_10'], 1)

    def test_empty_or_unmapped_gold_cannot_inflate_metrics(self):
        self.assertIsNone(retrieval_metrics(['one'], {}, {}))
        with self.assertRaises(ValueError):
            retrieval_metrics([], {'a': []}, {'a': 'page'})

    def test_mapping_requires_source_identity_hash_page_and_exact_excerpt(self):
        item = {'evidence': [{'unit_id': 'a', 'doc_id': 'd', 'content_sha256': 'hash', 'quote': 'water available', 'page_number': 2}]}
        chunk = {'chunk_id': 'one', 'doc_id': 'd', 'content_sha256': 'hash', 'text': 'water\navailable here', 'page_numbers': [2]}
        self.assertEqual(evidence_mapping(item, [chunk]), {'a': ['one']})
        for change in ({'doc_id': 'other'}, {'content_sha256': 'stale'}, {'page_numbers': [1]}, {'text': 'not supported'}):
            self.assertEqual(evidence_mapping(item, [dict(chunk, **change)]), {'a': []})

    def test_refusal_and_adversarial_items_excluded_from_headline_denominator(self):
        metrics = retrieval_metrics(['one'], {'a': ['one']}, {'a': 'page'})
        rows = [{'category': 'single_hop', 'metrics': metrics}, {'category': 'unanswerable', 'metrics': None}, {'category': 'ambiguous_adversarial', 'metrics': metrics}]
        self.assertEqual(aggregate(rows)['answerable_denominator'], 1)
        self.assertIsNone(aggregate([])['recall_at_5'])


if __name__ == '__main__':
    unittest.main()
