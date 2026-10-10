"""Offline reranker engineering probes; no gold labels or live LLM evaluation."""
import json
import math
import statistics
from unittest.mock import patch
from answering import ROOT, Reranker, generate
from retrieval import Embedder, IndexStore
from smoke_retrieval import PROBES


def main():
    config = json.loads((ROOT / 'config/retrieval.json').read_text())
    answering = json.loads((ROOT / 'config/answering.json').read_text())
    embedder = Embedder(config)
    ranker = Reranker(answering)
    retriever = IndexStore(config, development_preview=True).retriever('heading_aware', embedder)
    probes = []
    with patch('socket.socket', side_effect=RuntimeError('Network blocked for engineering probes')):
        for probe in PROBES:
            result = ranker.rerank(retriever.search(probe['query'], park=probe['park'],
                                  page_type=probe.get('page_type'), as_of=probe.get('as_of')))
            assert len(result['contexts']) <= 5
            assert {c['chunk_id'] for c in result['reranked_candidates']} == {c['chunk_id'] for c in result['candidates']}
            outcome = generate(result, answering)
            assert outcome['status'] == 'refused'
            probes.append({'id': probe['id'], 'query': probe['query'], 'index_version': result['index_version'],
                           'candidate_count': result['candidate_count'], 'context_count': len(result['contexts']),
                           'reranking_latency_seconds': result['reranking_latency_seconds'],
                           'generation_gate_reason': outcome['reason'],
                           'top_results': [{'chunk_id': c['chunk_id'], 'url': c['chunk']['url'],
                                            'pre_rank': c['pre_rank'], 'post_rank': c['post_rank'],
                                            'scores': c['scores'], 'reranker_score': c['reranker_score']}
                                           for c in result['contexts']]})
    times = sorted(p['reranking_latency_seconds'] for p in probes if p['candidate_count'])
    report = {'purpose': 'Engineering checks, no human gold labels and no reranking quality lift measured.',
              'model': answering['reranker_model'], 'revision': answering['reranker_revision'],
              'network': 'blocked during all searches, reranking and generation gates',
              'runs': len(probes), 'context_limit_and_candidate_identity_checks_passed': True,
              'live_generation_calls': 0,
              'reranking_only_seconds': {'p50': statistics.median(times), 'p95': times[math.ceil(.95 * len(times)) - 1],
                                         'note': 'Small CPU smoke sample; excludes model loading, retrieval, generation and UI.'},
              'probes': probes}
    (ROOT / 'data/audit/answering_smoke.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'probes'}, indent=2))


if __name__ == '__main__':
    main()
