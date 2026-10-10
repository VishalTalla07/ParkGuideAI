"""Run approved dev retrieval baselines offline. Never executes test queries."""
import argparse
import copy
import json
import math
import platform
import statistics
import time
from datetime import datetime, timezone
from unittest.mock import patch

from evaluation import load_frozen_dev, evidence_mapping, retrieval_metrics, aggregate, file_sha
from retrieval import ROOT, Embedder, IndexStore
from answering import Reranker

SYSTEMS = {
    'B1': ('fixed_overlap', 'bm25', False),
    'B2': ('fixed_overlap', 'dense', False),
    'A1': ('heading_aware', 'dense', False),
    'A2': ('heading_aware', 'hybrid', False),
    'B3': ('heading_aware', 'hybrid', True),
    'heading_bm25': ('heading_aware', 'bm25', False),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='data/evaluation/dev_retrieval_v1')
    args = parser.parse_args()
    output = ROOT / args.output
    if output.exists():
        raise ValueError('Output already exists; preserve prior run and choose a new version')
    dev, frozen = load_frozen_dev(ROOT)
    config = json.loads((ROOT / 'config/retrieval.json').read_text())
    answering = json.loads((ROOT / 'config/answering.json').read_text())
    embedder = Embedder(config)
    store = IndexStore(config)
    retrievers = {s: store.retriever(s, embedder) for s in ('fixed_overlap', 'heading_aware')}
    ranker = Reranker(answering)
    mappings = {s: {x['id']: evidence_mapping(x, retrievers[s].chunks) for x in dev} for s in retrievers}
    missing = [(s, item, unit) for s, mapping in mappings.items() for item, units in mapping.items() for unit, ids in units.items() if not ids]
    if missing:
        raise ValueError(f'Gold evidence cannot be mapped: {missing}')
    output.mkdir(parents=True)
    (output / 'evidence_mapping.json').write_text(json.dumps(mappings, indent=2) + '\n')
    (output / 'configuration.json').write_text(json.dumps({'retrieval': config, 'answering': answering, 'systems': SYSTEMS}, indent=2) + '\n')
    rows, contexts = [], []
    start = time.perf_counter()
    with patch('socket.socket', side_effect=RuntimeError('Network blocked during dev retrieval')):
        for item in dev:
            searches = {}
            for label, (strategy, mode, rerank) in SYSTEMS.items():
                key = (strategy, mode)
                if key not in searches:
                    searches[key] = retrievers[strategy].search(item['question'], mode=mode)
                result = copy.deepcopy(searches[key])
                pre_ids = [c['chunk_id'] for c in result['candidates']]
                if rerank:
                    result = ranker.rerank(result)
                    ranking = result['reranked_candidates']
                else:
                    ranking = result['candidates']
                    result['contexts'] = ranking[:5]
                ids = [c['chunk_id'] for c in ranking]
                mapping = mappings[strategy][item['id']]
                sources = {e['unit_id']: e['doc_id'] for e in item['evidence']}
                metrics = retrieval_metrics(ids, mapping, sources)
                row = {'item_id': item['id'], 'category': item['category'], 'system': label,
                       'strategy': strategy, 'mode': mode, 'reranked': rerank,
                       'metrics': metrics, 'ranking': ids, 'pre_ranking': pre_ids,
                       'retrieval_seconds': result['latency_seconds'],
                       'reranking_seconds': result.get('reranking_latency_seconds', 0),
                       'candidate_count': len(ids)}
                rows.append(row)
                # Save the exact inputs for future answer baselines, dev only.
                if label in ('B1', 'B2', 'B3'):
                    contexts.append({'item_id': item['id'], 'system': label, 'result': result})
            print(f"DEV {item['id']}: completed six retrieval configurations", flush=True)
    summary = {}
    for label in SYSTEMS:
        group = [r for r in rows if r['system'] == label]
        times = sorted(r['retrieval_seconds'] + r['reranking_seconds'] for r in group)
        summary[label] = {**aggregate(group), 'latency_seconds': {'p50': statistics.median(times), 'p95': times[math.ceil(.95 * len(times)) - 1]}}
    report = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'split': 'dev',
              'dev_items': len(dev), 'test_queries_executed': 0, 'generation_calls': 0,
              'network_access': 'blocked during query execution', 'run_seconds': time.perf_counter() - start,
              'corpus_sha256': frozen['corpus_sha256'], 'golden_set_sha256': frozen['golden_set_sha256'],
              'split_sha256': frozen['split_sha256'], 'index_version': next(iter(retrievers.values())).manifest['version'],
              'code_sha256': {n: file_sha(ROOT / 'scripts' / n) for n in ('evaluation.py', 'evaluate_dev.py', 'retrieval.py', 'answering.py')},
              'hardware': {'system': platform.system(), 'machine': platform.machine(), 'cpu_threads': config['cpu_threads']},
              'filters': 'None for all systems; park names must be resolved through retrieval, not gold labels.',
              'latency_note': 'Retrieval plus reranking only, 24 sequential dev queries; shared search results reused for A2/B3. Includes first-query cold effects; excludes model load, generation and UI.',
              'scoring_note': 'Macro averages across 17 ordinary answerable dev items. Ambiguous/adversarial item with evidence is scored separately per item, excluded from headline averages. MRR limited to candidate depth 20. Binary novel-evidence nDCG uses exact best achievable ordering of gold-mapped chunk unit sets.',
              'systems': summary}
    (output / 'results.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (output / 'generation_inputs.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in contexts))
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
