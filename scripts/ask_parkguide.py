"""Retrieve and rerank locally; optionally generate against an approved corpus."""
import argparse
import json
from pathlib import Path
from answering import Reranker, generate
from retrieval import ROOT, Embedder, IndexStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query')
    parser.add_argument('--park', choices=['yose', 'yell', 'grca'])
    parser.add_argument('--page-type', choices=['html', 'pdf'])
    parser.add_argument('--as-of')
    parser.add_argument('--development-preview', action='store_true')
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--json-output', type=Path)
    args = parser.parse_args()
    config = json.loads((ROOT / 'config/retrieval.json').read_text())
    answering = json.loads((ROOT / 'config/answering.json').read_text())
    embedder = Embedder(config)
    retriever = IndexStore(config, development_preview=args.development_preview).retriever('heading_aware', embedder)
    result = Reranker(answering).rerank(retriever.search(args.query, park=args.park, page_type=args.page_type, as_of=args.as_of))
    if args.generate:
        result['generated_answer'] = generate(result, answering)
        print(result['generated_answer']['answer'])
        print('Status:', result['generated_answer']['status'], result['generated_answer']['reason'])
    else:
        print('Retrieved evidence preview — not a generated answer.')
        for c in result['contexts']:
            print(f"\n{c['pre_rank']} -> {c['post_rank']}: {c['chunk']['title']} (score {c['reranker_score']:.3f})")
            print(c['chunk']['url'])
            print(c['chunk']['text'][:500])
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
