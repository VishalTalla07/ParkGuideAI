import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from chunk_sources import build_snapshot, digest
from retrieval import IndexStore, eligible, validate_vectors, where_clause


class FakeEmbedder:
    info = {'model': 'synthetic-test-vectors', 'dimension': 384, 'revision': 'fixture-v1'}

    def encode(self, texts):
        vectors = []
        for text in texts:
            text = text.lower()
            vector = np.zeros(384, dtype=np.float32)
            vector[383] = .01
            for index, words in enumerate([('camp', 'reservation'), ('dogs', 'pets'), ('fish', 'fishing'), ('permit', 'backcountry')]):
                vector[index] = sum(text.count(word) for word in words)
            vectors.append(vector / np.linalg.norm(vector))
        return np.asarray(vectors)


def fixture_documents():
    documents = []
    for park, name, text in [('yose', 'Camping', 'Camp reservations are required. Book the campground online.'),
                              ('yose', 'Pets', 'Dogs and pets must be leashed. Dogs are restricted on trails.'),
                              ('yell', 'Fishing', 'Fishing requires a fishing permit. Fish responsibly.'),
                              ('grca', 'Backcountry', 'Backcountry permits are required for overnight stays.')]:
        url = f'https://www.nps.gov/{park}/planyourvisit/{name.lower()}.htm'
        documents.append({'doc_id': hashlib.sha256(url.encode()).hexdigest(), 'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
                          'url': url, 'text': text, 'blocks': [{'type': 'paragraph', 'heading_path': [name], 'text': text}], 'tables': [],
                          'title': name, 'park': park, 'page_type': 'html', 'crawl_timestamp': '2026-10-10T18:00:00+00:00',
                          'raw_sha256': 'b' * 64, 'privacy_review_status': 'pending', 'temporal_review_status': 'pending',
                          'coverage_period': {'start': '2026-01-01', 'end': '2026-12-31'} if name == 'Fishing' else None})
    return documents


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.config = json.loads((ROOT / 'config/retrieval.json').read_text())
        self.chunk_config = json.loads((ROOT / 'config/chunking.json').read_text())
        self.source = self.base / 'documents.jsonl'
        self.source.write_text(''.join(json.dumps(d) + '\n' for d in fixture_documents()))
        self.chunk_summary = build_snapshot(self.source, self.chunk_config, self.base / 'chunks')
        self.snapshot = self.base / 'chunks/versions' / self.chunk_summary['version']
        self.embedder = FakeEmbedder()
        self.store = IndexStore(self.config, self.base / 'indexes', development_preview=True)

    def build(self):
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            return self.store.build(self.snapshot, self.embedder)

    def test_vector_validation(self):
        with self.assertRaises(ValueError):
            validate_vectors(np.zeros((1, 384)), 384)
        with self.assertRaises(ValueError):
            validate_vectors(np.ones((1, 383)), 384)
        with self.assertRaises(ValueError):
            validate_vectors(np.full((1, 384), np.nan), 384)

    def test_dense_cosine_and_keyword_relevance(self):
        self.build()
        retriever = self.store.retriever('heading_aware', self.embedder)
        for mode in ('dense', 'bm25', 'hybrid'):
            result = retriever.search('dogs pets', mode)
            self.assertEqual(result['candidates'][0]['chunk']['title'], 'Pets')
            self.assertTrue(result['candidate_pool_shortfall'])
        result = retriever.search('dogs pets', 'dense')
        self.assertGreater(result['candidates'][0]['scores']['dense_score'], .99)

    def test_same_filters_for_dense_bm25_and_hybrid(self):
        self.build()
        retriever = self.store.retriever('fixed_overlap', self.embedder)
        for mode in ('dense', 'bm25', 'hybrid'):
            result = retriever.search('fishing', mode, park='yell', page_type='html')
            self.assertEqual(result['eligible_chunks'], 1)
            self.assertTrue(all(c['chunk']['park'] == 'yell' and c['chunk']['page_type'] == 'html' for c in result['candidates']))

    def test_temporal_filter_excludes_unknown_and_wrong_period(self):
        self.build()
        retriever = self.store.retriever('heading_aware', self.embedder)
        result = retriever.search('fishing', 'hybrid', as_of='2026-10-10')
        self.assertEqual(result['eligible_chunks'], 1)
        self.assertEqual(result['candidates'][0]['chunk']['title'], 'Fishing')
        result = retriever.search('fishing', 'hybrid', as_of='2027-01-01')
        self.assertEqual(result['candidate_count'], 0)
        self.assertTrue(result['candidate_pool_shortfall'])

    def test_hybrid_fusion_unique_ids_and_scores(self):
        self.build()
        result = self.store.retriever('heading_aware', self.embedder).search('dogs pets', 'hybrid')
        ids = [item['chunk_id'] for item in result['candidates']]
        self.assertEqual(len(set(ids)), len(ids))
        best = result['candidates'][0]['scores']
        self.assertAlmostEqual(best['rrf_score'], 2 / 61)
        self.assertEqual(best['dense_rank'], 1)
        self.assertEqual(best['bm25_rank'], 1)

    def test_keyword_zero_overlap_does_not_return_arbitrary_ties(self):
        self.build()
        result = self.store.retriever('heading_aware', self.embedder).search('quasarxylophone', 'bm25')
        self.assertEqual(result['candidate_count'], 0)

    def test_approved_index_requires_review(self):
        approved = IndexStore(self.config, self.base / 'indexes')
        with self.assertRaises(ValueError):
            approved.build(self.snapshot, self.embedder)
        self.assertFalse(approved.pointer.exists())
        self.assertEqual(len(approved.client.list_collections()), 0)

    def test_manifest_sparse_and_vector_id_agreement(self):
        summary = self.build()
        directory = self.store.directory / 'versions' / summary['version']
        manifest = self.store.validate(directory)
        for entry in manifest['strategies'].values():
            collection = self.store.client.get_collection(entry['collection'], embedding_function=None)
            self.assertEqual(self.store.all_ids(collection), set(entry['chunk_ids']))

    def test_migration_deletes_old_vector_collection_and_sparse_snapshot(self):
        first = self.build()
        old_manifest = self.store.validate(self.store.directory / 'versions' / first['version'])
        alternate = copy.deepcopy(self.chunk_config)
        alternate['target_body_tokens'] = 160
        chunk_result = build_snapshot(self.source, alternate, self.base / 'chunks')
        next_source = self.base / 'chunks/versions' / chunk_result['version']
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            second = self.store.build(next_source, self.embedder)
        self.assertGreater(second['migration']['old_only_ids_removed'], 0)
        self.assertFalse((self.store.directory / 'versions' / first['version']).exists())
        new_names = {c.name for c in self.store.client.list_collections()}
        self.assertTrue(all(entry['collection'] not in new_names for entry in old_manifest['strategies'].values()))
        self.assertEqual(len(new_names), 2)
        self.assertEqual(second['migration']['orphan_sparse_versions'], 0)

    def test_failed_index_activation_preserves_previous_and_cleans_new(self):
        first = self.build()
        alternate = copy.deepcopy(self.chunk_config)
        alternate['target_body_tokens'] = 160
        next_chunks = build_snapshot(self.source, alternate, self.base / 'chunks')
        next_source = self.base / 'chunks/versions' / next_chunks['version']
        with patch.object(self.store, '_activate', side_effect=OSError('Simulated activation failure')):
            with self.assertRaises(OSError):
                self.store.build(next_source, self.embedder)
        self.assertEqual(json.loads(self.store.pointer.read_text())['version'], first['version'])
        self.assertEqual(len(self.store.client.list_collections()), 2)
        self.assertEqual([p.name for p in (self.store.directory / 'versions').iterdir()], [first['version']])
        self.store.validate(self.store.directory / 'versions' / first['version'])

    def test_invalid_filters_rejected(self):
        with self.assertRaises(ValueError):
            where_clause(park='unknown')
        with self.assertRaises(ValueError):
            where_clause(page_type='image')
        with self.assertRaises(ValueError):
            where_clause(as_of='yesterday')
        with self.assertRaises(ValueError):
            where_clause(as_of='20261010')

    def test_interrupted_build_collection_is_removed_before_retry(self):
        identity = {'chunk_version': self.chunk_summary['version'], 'config': self.config, 'mode': self.store.mode, 'embedder': self.embedder.info}
        version = digest(identity)
        name = f'pg_fixed_overlap_{version[:24]}'
        self.store.client.create_collection(name, embedding_function=None)
        result = self.build()
        self.assertEqual(result['version'], version)
        self.assertEqual(len(self.store.client.list_collections()), 2)
        self.store.validate(self.store.directory / 'versions' / version)

    def test_stale_retriever_rejected_after_migration(self):
        self.build()
        old = self.store.retriever('heading_aware', self.embedder)
        alternate = copy.deepcopy(self.chunk_config)
        alternate['target_body_tokens'] = 160
        chunks = build_snapshot(self.source, alternate, self.base / 'chunks')
        self.store.build(self.base / 'chunks/versions' / chunks['version'], self.embedder)
        with self.assertRaises(ValueError):
            old.search('pets', 'hybrid')


if __name__ == '__main__':
    unittest.main()
