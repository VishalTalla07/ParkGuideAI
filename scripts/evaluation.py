"""Pure offline evaluation helpers; no model calls or index mutations."""
from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path

from validate_evaluation import validate

ANSWERABLE = {'single_hop', 'multi_hop', 'comparative', 'temporal'}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_frozen_dev(root):
    """Fail closed on modified gold, split, corpus, or approval-bound facts."""
    root = Path(root)
    base = root / 'data/evaluation'
    frozen = json.loads((base / 'freeze_manifest.json').read_text())
    for name, key in [('golden_set.jsonl', 'golden_set_sha256'), ('split.json', 'split_sha256')]:
        if file_sha(base / name) != frozen[key]:
            raise ValueError(f'Frozen artifact changed: {name}')
    corpus = root / 'data/normalized/documents.jsonl'
    if file_sha(corpus) != frozen['corpus_sha256']:
        raise ValueError('Frozen corpus changed; review and version the gold set first')
    items = [json.loads(line) for line in (base / 'golden_set.jsonl').read_text().splitlines()]
    documents = [json.loads(line) for line in corpus.read_text().splitlines()]
    manifest = json.loads((base / 'split.json').read_text())
    errors = validate(items, documents, frozen['corpus_sha256'], manifest, require_verified=True)
    if errors:
        raise ValueError('; '.join(errors))
    for item in items:
        record = json.loads((root / item['review']['approval_record']).read_text())
        facts = {k: v for k, v in item.items() if k != 'review'}
        sha = hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if record['approved_item_sha256'].get(item['id']) != sha or record['corpus_sha256'] != frozen['corpus_sha256']:
            raise ValueError(f"Approval does not bind current facts: {item['id']}")
    # Test items are validated for integrity, never returned for query execution.
    dev = [x for x in items if x['split'] == 'dev']
    if len(dev) != 24 or {x['id'] for x in dev} != set(manifest['dev']):
        raise ValueError('Development split mismatch')
    return dev, frozen


def evidence_mapping(item, chunks):
    mapped = {}
    for e in item['evidence']:
        quote = ' '.join(e['quote'].split())
        mapped[e['unit_id']] = [c['chunk_id'] for c in chunks
            if c['doc_id'] == e['doc_id'] and c['content_sha256'] == e['content_sha256']
            and quote in ' '.join(c['text'].split())
            and (e['page_number'] is None or e['page_number'] in c['page_numbers'])]
    return mapped


def retrieval_metrics(ranking, mapping, unit_sources):
    """Binary novel-evidence DCG; ideal ordering is exact over mapped unit sets.

    A chunk gets binary gain only when it contributes a previously unseen unit.
    IDCG is the best achievable ordering of relevant chunk evidence sets, so a
    chunk containing two units does not create an impossible two-chunk ideal.
    """
    if not mapping:
        return None
    if any(not ids for ids in mapping.values()):
        raise ValueError('Cannot score an unmapped gold evidence unit')
    keys = sorted(mapping)
    reverse = {}
    for bit, key in enumerate(keys):
        for cid in mapping[key]:
            reverse[cid] = reverse.get(cid, 0) | (1 << bit)
    possible_masks = tuple(sorted(set(reverse.values())))

    @lru_cache(None)
    def ideal(position, covered):
        if position == 10:
            return 0.0
        return max((1 / math.log2(position + 2) + ideal(position + 1, covered | mask)
                    for mask in possible_masks if mask & ~covered), default=0.0)

    coverage = {}
    covered = 0
    dcg = 0.0
    reciprocal_rank = 0.0
    for rank, cid in enumerate(ranking[:20], 1):
        mask = reverse.get(cid, 0)
        if mask and not reciprocal_rank:
            reciprocal_rank = 1 / rank
        if rank <= 10 and mask & ~covered:
            dcg += 1 / math.log2(rank + 1)
        covered |= mask
        coverage[rank] = covered
    def found(k):
        return coverage.get(min(k, len(ranking), 20), 0)
    def all_sources(k):
        recovered = {unit_sources[key] for bit, key in enumerate(keys) if found(k) & (1 << bit)}
        return recovered == set(unit_sources.values())
    return {'recall_at_5': found(5).bit_count() / len(keys),
            'recall_at_20': found(20).bit_count() / len(keys),
            'mrr_at_20': reciprocal_rank, 'ndcg_at_10': dcg / ideal(0, 0),
            'all_evidence_at_5': found(5).bit_count() == len(keys),
            'all_required_sources_at_5': all_sources(5),
            'all_required_sources_at_20': all_sources(20)}


def aggregate(rows):
    scored = [r for r in rows if r['metrics'] is not None and r['category'] in ANSWERABLE]
    names = ('recall_at_5', 'recall_at_20', 'mrr_at_20', 'ndcg_at_10', 'all_evidence_at_5')
    result = {'answerable_denominator': len(scored),
              **{name: sum(r['metrics'][name] for r in scored) / len(scored) if scored else None for name in names}}
    multi = [r for r in scored if r['category'] == 'multi_hop']
    result['multi_hop_denominator'] = len(multi)
    for k in (5, 20):
        name = f'all_required_sources_at_{k}'
        result['multi_hop_' + name] = sum(r['metrics'][name] for r in multi) / len(multi) if multi else None
    result['scored_categories'] = dict(Counter(r['category'] for r in scored))
    return result
