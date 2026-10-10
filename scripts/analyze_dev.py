"""Analyze saved development retrieval results; never queries models or test items."""
import argparse
from collections import defaultdict
import json
import math
import random

from evaluation import ANSWERABLE, load_frozen_dev
from retrieval import ROOT


def paired_interval(before, after, metric, samples=10000, seed=633):
    strata = defaultdict(list)
    for key in sorted(before):
        strata[before[key]['category']].append(float(after[key]['metrics'][metric]) - float(before[key]['metrics'][metric]))
    rng = random.Random(seed)
    n = sum(map(len, strata.values()))
    boot = sorted(sum(sum(rng.choice(values) for _ in values) for values in strata.values()) / n for _ in range(samples))
    observed = sum(sum(v) for v in strata.values()) / n
    return {'difference': observed, 'ci95': [boot[math.floor(.025 * (samples - 1))], boot[math.ceil(.975 * (samples - 1))]], 'items': n}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', default='data/evaluation/dev_retrieval_v1')
    args = parser.parse_args()
    dev, _ = load_frozen_dev(ROOT)
    by_id = {x['id']: x for x in dev}
    directory = ROOT / args.run
    report = json.loads((directory / 'summary.json').read_text())
    rows = [json.loads(x) for x in (directory / 'results.jsonl').read_text().splitlines()]
    if any(r['item_id'] not in by_id for r in rows):
        raise ValueError('Results contain non-dev items')
    systems = {label: {r['item_id']: r for r in rows if r['system'] == label and r['category'] in ANSWERABLE} for label in report['systems']}
    comparisons = {}
    for before, after in [('B1', 'B2'), ('B2', 'A1'), ('A1', 'A2'), ('A2', 'B3')]:
        a, b = systems[before], systems[after]
        intervals = {m: paired_interval(a, b, m) for m in ('recall_at_5', 'ndcg_at_10', 'all_evidence_at_5')}
        losses = sum(a[k]['metrics']['all_evidence_at_5'] and not b[k]['metrics']['all_evidence_at_5'] for k in a)
        wins = sum(not a[k]['metrics']['all_evidence_at_5'] and b[k]['metrics']['all_evidence_at_5'] for k in a)
        discordant = losses + wins
        p = min(1.0, 2 * sum(math.comb(discordant, i) for i in range(min(losses, wins) + 1)) / 2 ** discordant) if discordant else 1.0
        comparisons[before + ' -> ' + after] = {'paired_bootstrap': intervals, 'mcnemar_exact_all_evidence_at_5': {'wins': wins, 'losses': losses, 'two_sided_p': p}}
    uncertainty = {'split': 'dev', 'seed': 633, 'resamples': 10000, 'comparisons': comparisons,
                   'note': 'Exploratory paired, category-stratified item bootstrap and exact McNemar. Small categories and related question families limit precision/independence; intervals are not confirmatory test-set evidence.'}
    (directory / 'uncertainty.json').write_text(json.dumps(uncertainty, indent=2) + '\n')
    table = ['# Development retrieval results', '', 'Offline run over all 24 dev questions. Metrics use the 17 ordinary answerable questions; five unanswerable and two ambiguous/adversarial questions are excluded from headline retrieval means. No test query was executed.', '', '| System | Recall@5 | Recall@20 | MRR@20 | nDCG@10 | Complete evidence@5 | Multi-hop source coverage@5 |', '|---|---:|---:|---:|---:|---:|---:|']
    for label, v in report['systems'].items():
        values = [v[x] for x in ('recall_at_5', 'recall_at_20', 'mrr_at_20', 'ndcg_at_10', 'all_evidence_at_5', 'multi_hop_all_required_sources_at_5')]
        table.append('| ' + label + ' | ' + ' | '.join(f'{x:.3f}' for x in values) + ' |')
    table += ['', 'B1 = fixed/BM25; B2 = fixed/dense; A1 = heading/dense; A2 = heading/hybrid; B3 = heading/hybrid/reranked. `heading_bm25` is a diagnostic comparison. B0 has no retrieval metrics and has not been run.', '', 'Recall is the mean fraction of approved evidence units recovered per item, not answer accuracy. Matching requires source identity, content hash and the exact approved quote (whitespace normalized); other semantically sufficient passages are not automatically labeled relevant. Binary nDCG gives no credit to repeated units, with an exact achievable ideal ordering. MRR is truncated to the 20-candidate pool. Multi-hop coverage has only four items.', '', 'B3 supplies complete approved evidence in its top five for 13/17 ordinary answerable questions and both required source pages for 2/4 multi-hop questions. Candidate Recall@20 is 0.941; reranking cannot recover evidence absent from its input pool.', '', 'Paired exploratory uncertainty is saved in `uncertainty.json`. Small counts, question-family correlations and repeated development use limit any improvement claim. Generated answer correctness, citation semantic support, refusal rates, B0 leakage and billed cost remain unscored.', '', '## B3 misses requiring review', '']
    for r in rows:
        if r['system'] == 'B3' and r['metrics'] and not r['metrics']['all_evidence_at_5']:
            hint = 'candidate retrieval miss' if r['metrics']['recall_at_20'] < 1 else 'evidence ranked outside final top five'
            table += [f"- {r['item_id']}: {hint}; Recall@5={r['metrics']['recall_at_5']:.2f}, Recall@20={r['metrics']['recall_at_20']:.2f}."]
    (directory / 'retrieval_report.md').write_text('\n'.join(table) + '\n')
    mapping = json.loads((directory / 'evidence_mapping.json').read_text())
    failures = [r for r in rows if r['metrics'] and r['category'] in ANSWERABLE and not r['metrics']['all_evidence_at_5']]
    # Prefer distinct questions, then add system-specific failure events if needed.
    ordered, seen = [], set()
    for r in failures:
        if r['item_id'] not in seen:
            ordered.append(r); seen.add(r['item_id'])
    ordered += [r for r in failures if r not in ordered]
    selected = ordered[:15]
    review = ['# Development retrieval failure review', '', 'These are candidate failure events for human inspection, not completed human labels. Inspect required facts, candidate passages and final ranking before accepting a label. Quote-based relevance can miss semantically equivalent evidence. At least 15 actual failures need human labels in the final project report.', '']
    annotations = []
    for i, r in enumerate(selected, 1):
        hint = 'retrieval_miss' if r['metrics']['recall_at_20'] < 1 else 'rank_failure'
        review += [f"## F-{i:02}: {r['item_id']} / {r['system']}", '', by_id[r['item_id']]['question'], '', f"Candidate label: `{hint}`; human decision pending.", '', *['- Required: ' + f for f in by_id[r['item_id']]['required_facts']], '']
        for unit, ids in mapping[r['strategy']][r['item_id']].items():
            ranks = [r['ranking'].index(cid) + 1 for cid in ids if cid in r['ranking']]
            review += [f"- Unit `{unit}`: earliest mapped rank {min(ranks) if ranks else 'outside top 20'}."]
        review += ['', 'Human label / supporting passage / correction / reviewer / date: ____________________', '']
        annotations.append({'failure_id': f'F-{i:02}', 'item_id': r['item_id'], 'system': r['system'], 'suggested_label': hint, 'human_label': None, 'reviewer': None, 'reviewed_at': None})
    (directory / 'failure_review.md').write_text('\n'.join(review))
    (directory / 'failure_annotations.json').write_text(json.dumps(annotations, indent=2) + '\n')
    print(json.dumps({'comparisons': len(comparisons), 'candidate_failure_events': len(selected), 'distinct_failure_questions': len({r['item_id'] for r in selected}), 'human_failure_labels': 0}))


if __name__ == '__main__':
    main()
