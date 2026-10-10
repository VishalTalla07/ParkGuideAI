"""Engineering smoke probes only: no gold answers or retrieval-quality scoring."""
import json
import math
import platform
import statistics
from unittest.mock import patch

from retrieval import ROOT, STRATEGIES, Embedder, IndexStore

PROBES = [
    {'id': 'camping', 'query': 'How do I reserve a campground in Yosemite?', 'park': 'yose'},
    {'id': 'fees', 'query': 'What are the entrance fees for Yellowstone?', 'park': 'yell'},
    {'id': 'permits', 'query': 'Do I need a backcountry permit for overnight camping at Grand Canyon?', 'park': 'grca'},
    {'id': 'accessibility_table', 'query': 'Which Yosemite campgrounds have accessible sites?', 'park': 'yose', 'page_type': 'html'},
    {'id': 'operating_dates_table', 'query': 'When is Bridge Bay Campground open in 2026?', 'park': 'yell', 'page_type': 'html'},
    {'id': 'pdf_small_pool', 'query': 'What distances are listed on the Grand Canyon Rim Trail?', 'park': 'grca', 'page_type': 'pdf'},
    {'id': 'dated_pdf', 'query': 'What does the fall guide say about Tioga Road?', 'park': 'yose', 'page_type': 'pdf', 'as_of': '2026-10-10'},
    {'id': 'unknown_period', 'query': 'What are the campground operating dates?', 'park': 'yell', 'as_of': '2027-01-01'},
]


def main():
    config = json.loads((ROOT / 'config/retrieval.json').read_text())
    embedder = Embedder(config)
    store = IndexStore(config, development_preview=True)
    probes = []
    with patch('socket.socket', side_effect=RuntimeError('No network access during retrieval smoke probes')):
        for strategy in STRATEGIES:
            retriever = store.retriever(strategy, embedder)
            for probe in PROBES:
                for mode in ('dense', 'bm25', 'hybrid'):
                    result = retriever.search(probe['query'], mode, probe.get('park'), probe.get('page_type'), probe.get('as_of'))
                    if len({c['chunk_id'] for c in result['candidates']}) != result['candidate_count']:
                        raise RuntimeError('Duplicate retrieved chunk IDs')
                    for candidate in result['candidates']:
                        chunk = candidate['chunk']
                        if chunk['park'] != probe['park'] or probe.get('page_type') and chunk['page_type'] != probe['page_type']:
                            raise RuntimeError('Retrieved evidence violated filters')
                    entry = {k: v for k, v in result.items() if k != 'candidates'}
                    entry['probe_id'] = probe['id']
                    entry['top_results'] = [{'chunk_id': c['chunk_id'], 'title': c['chunk']['title'], 'url': c['chunk']['url'],
                                             'heading_path': c['chunk']['heading_path'], 'scores': c['scores'],
                                             'excerpt': c['chunk']['text'][:240]} for c in result['candidates'][:5]]
                    probes.append(entry)
    times = sorted(p['latency_seconds'] for p in probes)
    report = {'purpose': 'Engineering smoke probes, not the human-verified 40-item golden set and not retrieval-quality evaluation.',
              'network_access': 'blocked during query execution', 'index_mode': store.mode,
              'probe_queries': len(PROBES), 'runs': len(probes), 'strategy_modes': 6,
              'filter_and_unique_id_checks_passed': True, 'candidate_pool_shortfall_runs': sum(p['candidate_pool_shortfall'] for p in probes),
              'retrieval_only_seconds': {'p50': statistics.median(times), 'p95': times[math.ceil(len(times) * .95) - 1],
                                         'note': 'Small smoke sample; excludes model loading, reranking, generation, and UI. Not end-to-end latency.'},
              'hardware': {'system': platform.system(), 'machine': platform.machine(), 'cpu_threads': config['cpu_threads']},
              'probes': probes}
    (ROOT / 'data/audit/retrieval_smoke.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'probes'}, indent=2))


if __name__ == '__main__':
    main()
