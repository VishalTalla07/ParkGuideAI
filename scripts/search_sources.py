"""Inspect retrieved evidence; this command does not generate chatbot answers."""
import argparse
import json
from pathlib import Path

from retrieval import ROOT, Embedder, IndexStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query')
    parser.add_argument('--mode', choices=['dense', 'bm25', 'hybrid'], default='hybrid')
    parser.add_argument('--strategy', choices=['fixed_overlap', 'heading_aware'], default='heading_aware')
    parser.add_argument('--park', choices=['yose', 'yell', 'grca'])
    parser.add_argument('--page-type', choices=['html', 'pdf'])
    parser.add_argument('--as-of', help='ISO date; requires explicit known coverage, excluding unknown periods')
    parser.add_argument('--development-preview', action='store_true')
    parser.add_argument('--json-output', type=Path)
    parser.add_argument('--show', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.show <= 20:
        parser.error('--show must be between 1 and 20')
    config = json.loads((ROOT / 'config/retrieval.json').read_text())
    embedder = Embedder(config)
    store = IndexStore(config, development_preview=args.development_preview)
    if not store.pointer.exists():
        parser.error('This index is not built yet; run build_index.py in the matching mode first.')
    retriever = store.retriever(args.strategy, embedder)
    result = retriever.search(args.query, args.mode, args.park, args.page_type, args.as_of)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    print(f"{result['candidate_count']} candidates; {result['eligible_chunks']} eligible chunks; pool shortfall: {result['candidate_pool_shortfall']}")
    if result['warning']:
        print(result['warning'])
    print(result['temporal_note'])
    for candidate in result['candidates'][:args.show]:
        chunk = candidate['chunk']
        print(f"\n{candidate['rank']}. {chunk['title']} — {' > '.join(chunk['heading_path'])}")
        print(chunk['url'])
        print(json.dumps(candidate['scores'], sort_keys=True))
        print(chunk['text'][:700])


if __name__ == '__main__':
    main()
