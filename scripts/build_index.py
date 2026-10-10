"""Build local vector and BM25/provenance snapshots from current chunks."""
import argparse
import json
from pathlib import Path

from retrieval import ROOT, Embedder, IndexStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--development-preview', action='store_true', help='Separate local preview of unreviewed chunks; never marks them approved')
    args = parser.parse_args()
    config = json.loads((ROOT / 'config/retrieval.json').read_text())
    active = json.loads((ROOT / 'data/chunks/active.json').read_text())['version']
    source = ROOT / 'data/chunks/versions' / active
    if not args.development_preview:
        records = [json.loads(line) for line in (source / 'heading_aware.jsonl').read_text().splitlines()]
        if not any(c.get('ready_for_index') and c.get('privacy_review_status') == 'approved' and c.get('temporal_review_status') == 'approved' for c in records):
            parser.error('No approved chunks yet. Use --development-preview for the separately labeled local engineering index.')
    embedder = Embedder(config)
    store = IndexStore(config, development_preview=args.development_preview)
    report = store.build(source, embedder)
    name = 'retrieval_preview_summary.json' if args.development_preview else 'retrieval_index_summary.json'
    receipt = ROOT / 'data/audit' / name
    if report.get('reused') and receipt.exists():
        original = json.loads(receipt.read_text())
        if original['version'] == report['version']:
            original['reused'] = True
            original['reuse_note'] = 'Existing snapshot revalidated; embedding statistics and build time describe its original build.'
            report = original
    receipt.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
