"""Persistent local dense/BM25/hybrid retrieval with auditable index snapshots."""
from datetime import date
import hashlib
from importlib.metadata import version as package_version
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

os.environ.setdefault('ANONYMIZED_TELEMETRY', 'False')
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')

import chromadb
from chromadb.config import Settings
from filelock import FileLock
import numpy as np
from rank_bm25 import BM25Okapi

from chunk_sources import ROOT, STRATEGIES, canonical_json, digest, validate_snapshot


def validate_vectors(vectors, dimension):
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != dimension or not np.isfinite(array).all():
        raise ValueError('Embedding dimensions or finite values are invalid')
    if not np.allclose(np.linalg.norm(array, axis=1), 1, atol=1e-5):
        raise ValueError('Embedding vectors must have unit L2 norm')
    return array


class Embedder:
    def __init__(self, config, root=ROOT):
        from sentence_transformers import SentenceTransformer
        import torch
        self.config = config
        directory = Path(root) / config['model_directory']
        manifest = json.loads((directory / 'download_manifest.json').read_text())
        if manifest['revision'] != config['model_revision'] or manifest['model'] != config['model']:
            raise ValueError('Embedding model revision mismatch')
        for name, expected in manifest['sha256'].items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
                raise ValueError(f'Embedding model file checksum mismatch: {name}')
        modules = json.loads((directory / 'modules.json').read_text())
        allowed = {'sentence_transformers.models.Transformer', 'sentence_transformers.models.Pooling', 'sentence_transformers.models.Normalize'}
        if any(module['type'] not in allowed for module in modules):
            raise ValueError('Unexpected model module; executable remote code is not allowed')
        chunk_config = json.loads((Path(root) / 'config/chunking.json').read_text())
        if manifest['sha256']['tokenizer.json'] != chunk_config['tokenizer']['sha256']:
            raise ValueError('Embedding and chunking tokenizers differ')
        torch.set_num_threads(config['cpu_threads'])
        self.model = SentenceTransformer(str(directory), device='cpu', local_files_only=True, trust_remote_code=False)
        if self.model.get_sentence_embedding_dimension() != config['dimension'] or self.model.max_seq_length != config['maximum_input_tokens']:
            raise ValueError('Embedding model shape/sequence length differs from configuration')
        self.info = {'model': config['model'], 'revision': config['model_revision'], 'dimension': config['dimension'],
                     'metric': config['metric'], 'L2_normalized': True, 'device': 'cpu',
                     'sentence_transformers': package_version('sentence-transformers'), 'torch': package_version('torch'),
                     'model_files_sha256': digest(manifest['sha256'])}

    def encode(self, texts):
        if not texts:
            return np.empty((0, self.config['dimension']), dtype=np.float32)
        inputs = self.model.tokenizer(texts, add_special_tokens=True, truncation=False, padding=False)['input_ids']
        if any(len(ids) > self.config['maximum_input_tokens'] for ids in inputs):
            raise ValueError('Input exceeds the model token limit; shorten it rather than silently truncating')
        result = self.model.encode(texts, batch_size=self.config['batch_size'], normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=False)
        return validate_vectors(result, self.config['dimension'])


def tokenize(text):
    return re.findall(r'[a-z0-9]+', text.lower())


def where_clause(park=None, page_type=None, as_of=None):
    clauses = []
    if park:
        if park not in ('yose', 'yell', 'grca'):
            raise ValueError('Unknown park filter')
        clauses.append({'park': park})
    if page_type:
        if page_type not in ('html', 'pdf'):
            raise ValueError('Unknown page-type filter')
        clauses.append({'page_type': page_type})
    if as_of:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
            raise ValueError('Use a YYYY-MM-DD date filter')
        day = date.fromisoformat(as_of).toordinal()
        clauses.extend([{'coverage_known': True}, {'coverage_start': {'$lte': day}}, {'coverage_end': {'$gte': day}}])
    return {'$and': clauses} if len(clauses) > 1 else clauses[0] if clauses else None


def eligible(chunk, park=None, page_type=None, as_of=None):
    if park and chunk['park'] != park or page_type and chunk['page_type'] != page_type:
        return False
    if as_of:
        coverage = chunk.get('coverage_period')
        return bool(coverage and coverage['start'] <= as_of <= coverage['end'])
    return True


def metadata(chunk):
    coverage = chunk.get('coverage_period')
    return {'park': chunk['park'], 'page_type': chunk['page_type'], 'doc_id': chunk['doc_id'],
            'url': chunk['url'], 'ready_for_index': chunk['ready_for_index'],
            'coverage_known': bool(coverage),
            'coverage_start': date.fromisoformat(coverage['start']).toordinal() if coverage else 0,
            'coverage_end': date.fromisoformat(coverage['end']).toordinal() if coverage else 0}


class IndexStore:
    def __init__(self, config, directory=None, development_preview=False):
        self.config = config
        if config['metric'] != 'cosine' or config['normalize_embeddings'] is not True or config['dimension'] != 384:
            raise ValueError('This implementation requires 384-dimensional normalized cosine embeddings')
        if config['candidate_pool'] < 20 or config['retriever_depth'] < config['candidate_pool'] or config['rrf_constant'] <= 0:
            raise ValueError('Require at least 20 candidates, adequate retrieval depth, and positive RRF constant')
        self.mode = 'development_preview' if development_preview else 'approved'
        self.directory = Path(directory or ROOT / 'data/index') / self.mode
        self.directory.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.directory / 'chroma'), settings=Settings(anonymized_telemetry=False))
        self.pointer = self.directory / 'active.json'

    def all_ids(self, collection):
        count = collection.count()
        return set(collection.get(limit=count, include=[])['ids']) if count else set()

    def validate(self, directory):
        directory = Path(directory)
        manifest = json.loads((directory / 'manifest.json').read_text())
        for strategy in STRATEGIES:
            entry = manifest['strategies'][strategy]
            records = directory / f'{strategy}.jsonl'
            if hashlib.sha256(records.read_bytes()).hexdigest() != entry['records_sha256']:
                raise ValueError('Sparse/provenance records checksum mismatch')
            chunks = [json.loads(line) for line in records.read_text().splitlines()]
            expected = set(entry['chunk_ids'])
            if {c['chunk_id'] for c in chunks} != expected or len(chunks) != len(expected):
                raise ValueError('Sparse IDs differ from index manifest')
            collection = self.client.get_collection(entry['collection'], embedding_function=None)
            if self.all_ids(collection) != expected:
                raise ValueError('Vector IDs differ from sparse/manifest IDs')
            if collection.configuration['hnsw']['space'] != 'cosine':
                raise ValueError('Vector store metric mismatch')
        return manifest

    def vectors(self, chunks, embedder):
        cache = self.directory.parent / 'embedding_cache' / digest(embedder.info)
        cache.mkdir(parents=True, exist_ok=True)
        vectors = [None] * len(chunks)
        missing = []
        for index, chunk in enumerate(chunks):
            path = cache / (chunk['input_sha256'] + '.npy')
            if path.exists():
                try:
                    vectors[index] = validate_vectors(np.load(path, allow_pickle=False).reshape(1, -1), self.config['dimension'])[0]
                except (ValueError, OSError):
                    missing.append(index)
            else:
                missing.append(index)
        for start in range(0, len(missing), self.config['batch_size']):
            indices = missing[start:start + self.config['batch_size']]
            encoded = embedder.encode([chunks[i]['embedding_input'] for i in indices])
            encoded = validate_vectors(encoded, self.config['dimension'])
            if len(encoded) != len(indices):
                raise ValueError('Embedding result count mismatch')
            for index, vector in zip(indices, encoded):
                vectors[index] = vector
                path = cache / (chunks[index]['input_sha256'] + '.npy')
                temporary = path.with_suffix('.tmp')
                with temporary.open('wb') as file:
                    np.save(file, vector, allow_pickle=False)
                os.replace(temporary, path)
            print(f'EMBED {min(start + self.config["batch_size"], len(missing))}/{len(missing)} new inputs', flush=True)
        return validate_vectors(np.asarray(vectors), self.config['dimension']), len(missing)

    def build(self, chunk_directory, embedder):
        with FileLock(self.directory / '.build.lock'):
            return self._build(Path(chunk_directory), embedder)

    def _build(self, source, embedder):
        start_time = time.perf_counter()
        validate_snapshot(source)
        chunk_manifest = json.loads((source / 'manifest.json').read_text())
        identity = {'chunk_version': chunk_manifest['version'], 'config': self.config, 'mode': self.mode, 'embedder': embedder.info}
        version = digest(identity)
        versions = self.directory / 'versions'
        versions.mkdir(exist_ok=True)
        previous = json.loads(self.pointer.read_text())['version'] if self.pointer.exists() else None
        previous_manifest = self.validate(versions / previous) if previous else None
        destination = versions / version
        if destination.exists():
            manifest = self.validate(destination)
            self._activate(version)
            self._cleanup(version, manifest)
            return {'version': version, 'mode': self.mode, 'reused': True, 'strategies': {s: len(manifest['strategies'][s]['chunk_ids']) for s in STRATEGIES}}
        # Recover an interrupted build before its deterministic collection names
        # are reused. Preserve every collection referenced by the active manifest.
        active_names = {entry['collection'] for entry in previous_manifest['strategies'].values()} if previous_manifest else set()
        for collection in self.client.list_collections():
            name = collection.name if hasattr(collection, 'name') else collection
            if name.startswith('pg_') and name not in active_names:
                self.client.delete_collection(name)
        for path in versions.iterdir():
            if path.name != previous and path.is_dir() and not path.is_symlink():
                if re.fullmatch('[a-f0-9]{64}', path.name) or path.name.startswith('.building-'):
                    shutil.rmtree(path)
        temporary = Path(tempfile.mkdtemp(prefix='.building-', dir=versions))
        created = []
        manifest = {'version': version, **identity, 'strategies': {}}
        metrics = {}
        try:
            for strategy in STRATEGIES:
                chunks = [json.loads(line) for line in (source / f'{strategy}.jsonl').read_text().splitlines()]
                if self.mode == 'approved':
                    chunks = [c for c in chunks if c.get('ready_for_index') and c.get('privacy_review_status') == 'approved' and c.get('temporal_review_status') == 'approved']
                if not chunks:
                    raise ValueError('No approved chunks. Corpus review is pending; use the separate development preview for local engineering tests.')
                for chunk in chunks:
                    if hashlib.sha256(chunk['embedding_input'].encode()).hexdigest() != chunk['input_sha256']:
                        raise ValueError('Chunk embedding-input checksum mismatch')
                vectors, misses = self.vectors(chunks, embedder)
                collection_name = f'pg_{strategy}_{version[:24]}'
                collection = self.client.create_collection(collection_name, embedding_function=None,
                                                            configuration={'hnsw': {'space': 'cosine'}},
                                                            metadata={'index_version': version, 'mode': self.mode})
                created.append(collection_name)
                for offset in range(0, len(chunks), 256):
                    batch = chunks[offset:offset + 256]
                    collection.add(ids=[c['chunk_id'] for c in batch], embeddings=vectors[offset:offset + 256].tolist(), metadatas=[metadata(c) for c in batch])
                records = temporary / f'{strategy}.jsonl'
                records.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in chunks))
                manifest['strategies'][strategy] = {'collection': collection_name, 'chunk_ids': [c['chunk_id'] for c in chunks],
                                                    'records_sha256': hashlib.sha256(records.read_bytes()).hexdigest()}
                norms = np.linalg.norm(vectors, axis=1)
                metrics[strategy] = {'chunks': len(chunks), 'documents': len({c['doc_id'] for c in chunks}), 'new_embeddings': misses,
                                     'dimension': vectors.shape[1], 'norm_min': float(norms.min()), 'norm_max': float(norms.max()),
                                     'all_finite': bool(np.isfinite(vectors).all()), 'cosine_self_similarity_min': float(np.min(np.sum(vectors * vectors, axis=1)))}
            (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            self.validate(temporary)
            os.replace(temporary, destination)
            self._activate(version)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            active = json.loads(self.pointer.read_text())['version'] if self.pointer.exists() else None
            if active != version:
                for name in created:
                    self.client.delete_collection(name)
                if destination.exists():
                    shutil.rmtree(destination)
            (self.directory / 'active.json.tmp').unlink(missing_ok=True)
            raise
        cleanup = self._cleanup(version, manifest)
        old_ids = set().union(*(set(e['chunk_ids']) for e in previous_manifest['strategies'].values())) if previous_manifest else set()
        current_ids = set().union(*(set(e['chunk_ids']) for e in manifest['strategies'].values()))
        return {'version': version, 'mode': self.mode, 'chunk_version': chunk_manifest['version'], 'model': embedder.info,
                'strategies': metrics, 'build_seconds': time.perf_counter() - start_time,
                'migration': {'previous_version': previous, 'old_only_ids_removed': len(old_ids - current_ids), **cleanup},
                'warning': 'Unreviewed local engineering preview; not approved corpus or evaluated retrieval quality.' if self.mode != 'approved' else None}

    def _activate(self, version):
        temporary = self.directory / 'active.json.tmp'
        temporary.write_text(json.dumps({'version': version}, indent=2) + '\n')
        os.replace(temporary, self.pointer)

    def _cleanup(self, version, manifest):
        expected_collections = {entry['collection'] for entry in manifest['strategies'].values()}
        removed = []
        for collection in self.client.list_collections():
            name = collection.name if hasattr(collection, 'name') else collection
            if name.startswith('pg_') and name not in expected_collections:
                self.client.delete_collection(name)
                removed.append(name)
        versions = self.directory / 'versions'
        for path in versions.iterdir():
            if path.name != version and path.is_dir() and not path.is_symlink():
                if re.fullmatch('[a-f0-9]{64}', path.name) or path.name.startswith('.building-'):
                    shutil.rmtree(path)
        self.validate(versions / version)
        names = {collection.name if hasattr(collection, 'name') else collection for collection in self.client.list_collections()}
        if names != expected_collections or {p.name for p in versions.iterdir()} != {version}:
            raise ValueError('Old vector collections or sparse snapshots remain')
        return {'removed_collections': sorted(removed), 'active_id_sets_verified': True, 'orphan_collections': 0, 'orphan_sparse_versions': 0}

    def retriever(self, strategy, embedder):
        with FileLock(self.directory / '.build.lock'):
            return self._retriever(strategy, embedder)

    def _retriever(self, strategy, embedder):
        if strategy not in STRATEGIES:
            raise ValueError('Unknown chunking strategy')
        active = json.loads(self.pointer.read_text())['version']
        directory = self.directory / 'versions' / active
        manifest = self.validate(directory)
        if manifest['config'] != self.config or manifest['embedder'] != embedder.info:
            raise ValueError('Query embedder/configuration differs from indexed model')
        chunks = [json.loads(line) for line in (directory / f'{strategy}.jsonl').read_text().splitlines()]
        return Retriever(self, strategy, manifest, chunks, embedder)


class Retriever:
    def __init__(self, store, strategy, manifest, chunks, embedder):
        self.store, self.strategy, self.manifest = store, strategy, manifest
        self.chunks, self.embedder = chunks, embedder
        self.by_id = {c['chunk_id']: c for c in chunks}
        self.tokens = [tokenize(c['embedding_input']) for c in chunks]
        self.bm25 = BM25Okapi(self.tokens, k1=store.config['bm25_k1'], b=store.config['bm25_b'])
        self.collection = store.client.get_collection(manifest['strategies'][strategy]['collection'], embedding_function=None)

    def search(self, query, mode='hybrid', park=None, page_type=None, as_of=None):
        with FileLock(self.store.directory / '.build.lock'):
            active = json.loads(self.store.pointer.read_text())['version']
            if active != self.manifest['version']:
                raise ValueError('Index changed; reload the retriever before searching')
            return self._search(query, mode, park, page_type, as_of)

    def _search(self, query, mode='hybrid', park=None, page_type=None, as_of=None):
        if mode not in ('dense', 'bm25', 'hybrid') or not query.strip():
            raise ValueError('Use a nonempty query and dense, bm25, or hybrid mode')
        start = time.perf_counter()
        where = where_clause(park, page_type, as_of)
        eligible_ids = {c['chunk_id'] for c in self.chunks if eligible(c, park, page_type, as_of)}
        depth = min(self.store.config['retriever_depth'], len(eligible_ids))
        dense, sparse = [], []
        if depth and mode in ('dense', 'hybrid'):
            vector = validate_vectors(self.embedder.encode([query]), self.store.config['dimension'])[0]
            results = self.collection.query(query_embeddings=[vector.tolist()], n_results=depth, where=where, include=['distances'])
            dense = [(key, 1 - float(distance)) for key, distance in zip(results['ids'][0], results['distances'][0])]
            if any(key not in eligible_ids for key, _ in dense):
                raise ValueError('Dense results violated eligibility filters')
        if depth and mode in ('bm25', 'hybrid'):
            query_tokens = tokenize(query)
            token_set = set(query_tokens)
            scores = self.bm25.get_scores(query_tokens)
            # A zero-overlap term set is not evidence, even if every score ties.
            sparse = [(chunk['chunk_id'], float(scores[i])) for i, chunk in enumerate(self.chunks)
                      if chunk['chunk_id'] in eligible_ids and token_set.intersection(self.tokens[i])]
            sparse.sort(key=lambda item: (-item[1], item[0]))
            sparse = sparse[:depth]
        ranks = {}
        for label, results in [('dense', dense), ('bm25', sparse)]:
            for rank, (key, score) in enumerate(results, 1):
                ranks.setdefault(key, {})[label + '_rank'] = rank
                ranks[key][label + '_score'] = score
        for scores in ranks.values():
            scores['rrf_score'] = sum(1 / (self.store.config['rrf_constant'] + scores[label + '_rank'])
                                       for label in ('dense', 'bm25') if label + '_rank' in scores)
        score_key = 'rrf_score' if mode == 'hybrid' else 'dense_score' if mode == 'dense' else 'bm25_score'
        keys = sorted(ranks, key=lambda key: (-ranks[key][score_key], key))[:self.store.config['candidate_pool']]
        candidates = [{'rank': rank, 'chunk_id': key, 'scores': ranks[key], 'chunk': self.by_id[key]} for rank, key in enumerate(keys, 1)]
        return {'query': query, 'mode': mode, 'strategy': self.strategy, 'index_version': self.manifest['version'],
                'index_mode': self.store.mode, 'filters': {'park': park, 'page_type': page_type, 'as_of': as_of},
                'eligible_chunks': len(eligible_ids), 'dense_candidate_count': len(dense), 'bm25_candidate_count': len(sparse),
                'candidate_count': len(candidates), 'candidate_pool_shortfall': len(candidates) < self.store.config['candidate_pool'],
                'latency_seconds': time.perf_counter() - start, 'candidates': candidates,
                'temporal_note': 'As-of filtering accepts only explicit known coverage periods; unknown dates are excluded.' if as_of else 'Cached evidence; source update dates do not prove current conditions.',
                'warning': 'Development preview of unreviewed corpus; results are retrieved evidence, not generated or verified answers.' if self.store.mode != 'approved' else None}
