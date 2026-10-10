"""Collect dev answers from saved retrieval inputs; no retries or test execution."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import statistics
from urllib.error import HTTPError, URLError

from answering import generate, QuotaReached
from evaluation import load_frozen_dev, file_sha
from generation_settings import generation_config, api_key, QUOTA_MESSAGE
from retrieval import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval-run', default='data/evaluation/dev_retrieval_v1')
    parser.add_argument('--system', choices=('B1', 'B2', 'B3'), default='B3')
    parser.add_argument('--output', default='data/evaluation/dev_answers_b3_v1')
    parser.add_argument('--resume-from', help='Explicit later run: skip completed answers from this saved batch; retry pending/error items only.')
    args = parser.parse_args()
    dev, frozen = load_frozen_dev(ROOT)
    by_id = {x['id']: x for x in dev}
    source = ROOT / args.retrieval_run
    report = json.loads((source / 'summary.json').read_text())
    for key in ('corpus_sha256', 'golden_set_sha256', 'split_sha256'):
        if report[key] != frozen[key]:
            raise ValueError('Retrieval run differs from frozen evaluation artifacts')
    index = json.loads((ROOT / 'data/index/approved/active.json').read_text())
    if index['version'] != report['index_version']:
        raise ValueError('Approved index changed since retrieval run')
    for name in ('answering.py', 'retrieval.py'):
        if file_sha(ROOT / 'scripts' / name) != report['code_sha256'][name]:
            raise ValueError(f'{name} changed since retrieval run; version a new dev run')
    config = generation_config()
    saved_config = json.loads((source / 'configuration.json').read_text())['answering']
    if config != saved_config or not api_key(config):
        raise ValueError('Generation settings changed or local credential is absent')
    inputs = [json.loads(line) for line in (source / 'generation_inputs.jsonl').read_text().splitlines()]
    selected = [x for x in inputs if x['system'] == args.system]
    if len(selected) != 24 or {x['item_id'] for x in selected} != set(by_id):
        raise ValueError('Generation inputs must contain exactly the 24 dev items')
    if any(x['result']['query'] != by_id[x['item_id']]['question'] for x in selected):
        raise ValueError('Saved query does not match approved development question')
    resume_record = None
    if args.resume_from:
        previous = ROOT / args.resume_from
        previous_manifest = json.loads((previous / 'manifest.json').read_text())
        expected = {'system': args.system, 'generation_inputs_sha256': file_sha(source / 'generation_inputs.jsonl'),
                    'configuration_sha256': file_sha(source / 'configuration.json'),
                    'golden_set_sha256': frozen['golden_set_sha256'], 'split_sha256': frozen['split_sha256'],
                    'generation_model': config['generation_model']}
        if any(previous_manifest.get(k) != v for k, v in expected.items()):
            raise ValueError('Resume batch differs from the current dev run')
        prior_rows = [json.loads(line) for line in (previous / 'answers.jsonl').read_text().splitlines()]
        if any(x['item_id'] not in by_id or x['system'] != args.system for x in prior_rows):
            raise ValueError('Resume records contain non-dev items or a different system')
        completed = {x['item_id'] for x in prior_rows if x['answer']['status'] in ('answered', 'refused')}
        selected = [x for x in selected if x['item_id'] not in completed]
        resume_record = {'directory': str(previous.relative_to(ROOT)),
                         'answers_sha256': file_sha(previous / 'answers.jsonl'),
                         'completed_ids_skipped': sorted(completed)}
    output = ROOT / args.output
    if output.exists():
        raise ValueError('Preserve prior answers; output must be a new version')
    output.mkdir(parents=True)
    manifest = {'split': 'dev', 'system': args.system, 'recorded_at': datetime.now(timezone.utc).isoformat(),
                'generation_provider': config['generation_provider'], 'generation_model': config['generation_model'],
                'corpus_sha256': frozen['corpus_sha256'], 'golden_set_sha256': frozen['golden_set_sha256'],
                'split_sha256': frozen['split_sha256'], 'index_version': report['index_version'],
                'generation_inputs_sha256': file_sha(source / 'generation_inputs.jsonl'),
                'configuration_sha256': file_sha(source / 'configuration.json'),
                'runner_sha256': file_sha(ROOT / 'scripts/generate_dev.py'),
                'test_queries_executed': 0, 'automatic_retries': 0, 'resume_from': resume_record}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    rows = []
    with (output / 'answers.jsonl').open('w') as stream:
        for entry in selected:
            item = by_id[entry['item_id']]
            try:
                answer = generate(entry['result'], config)
            except QuotaReached:
                answer = {'status': 'error', 'reason': 'quota_reached', 'answer': QUOTA_MESSAGE}
            except (HTTPError, URLError, TimeoutError):
                answer = {'status': 'error', 'reason': 'provider_error', 'answer': 'Answer service could not complete the request.'}
            row = {'item_id': item['id'], 'category': item['category'], 'expected_behavior': item['expected_behavior'],
                   'system': args.system, 'answer': answer,
                   'retrieval_seconds': entry['result']['latency_seconds'],
                   'reranking_seconds': entry['result'].get('reranking_latency_seconds', 0),
                   'human_score': {'status': 'pending', 'correctness': None, 'citation_support': None, 'failure_labels': []}}
            stream.write(json.dumps(row, ensure_ascii=False) + '\n'); stream.flush()
            rows.append(row)
            print(f"DEV {item['id']}: {answer['status']} ({answer['reason']})", flush=True)
            if answer['status'] == 'error':
                break  # quota and network errors stop the batch; never auto retry.
    statuses = dict(Counter(x['answer']['status'] for x in rows))
    times = sorted(x['retrieval_seconds'] + x['reranking_seconds'] + x['answer']['generation_latency_seconds']
                   for x in rows if 'generation_latency_seconds' in x['answer'])
    total_tokens = sum((x['answer'].get('usage') or {}).get('totalTokenCount', 0) for x in rows)
    summary = {**manifest, 'attempted_items': len(rows), 'completed_nonerror_items': sum(x['answer']['status'] != 'error' for x in rows),
               'remaining_ids': [x['item_id'] for x in selected if x['item_id'] not in {r['item_id'] for r in rows if r['answer']['status'] != 'error'}],
               'statuses': statuses, 'human_scored': 0, 'answer_accuracy': None, 'citation_semantic_accuracy': None,
               'provider_reported_total_tokens': total_tokens, 'api_cost_usd': None,
               'cost_note': 'Billing has not been verified; free-tier key configuration does not establish actual billed cost.',
               'pipeline_latency_seconds': {'p50': statistics.median(times) if times else None, 'p95': times[math.ceil(.95 * len(times)) - 1] if times else None},
               'latency_note': 'Sum of recorded retrieval, reranking and generation for each item; excludes model loading and UI; retrieval inputs reused.',
               'note': 'Status and exact quote checks are automated. Human correctness, semantic citation support and failure labels remain pending.'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    packet = ['# B3 development answer review', '', 'AI-generated dev answers; no answer accuracy has been scored. Review every required fact and each cited claim, including whether quotations support conditions. A refusal on an answerable question is an over-refusal only after checking context sufficiency; quota errors are operational errors.', '']
    for row in rows:
        item = by_id[row['item_id']]
        packet += [f"## {item['id']} · {item['category']}", '', item['question'], '', 'Expected behavior: ' + item['expected_behavior'], '', 'Required facts:', '', *['- ' + f for f in item['required_facts']], '', 'Model output:', '', row['answer']['answer'], '', 'Technical status: ' + row['answer']['status'] + ' / ' + str(row['answer']['reason']), '']
        for e in item['evidence']:
            packet += [f"Gold source: [{e['url']}]({e['url']}) (saved block {e['block_index']}).", '', '> ' + e['quote'].replace('\n', ' '), '']
        for claim in row['answer'].get('claims', []):
            packet += ['Generated claim: ' + claim['text'], '']
            for e in claim['evidence']:
                packet += ['Generated supporting quote (`' + e['chunk_id'] + '`):', '', '> ' + e['quote'].replace('\n', ' '), '']
        packet += ['Human correctness / citation support / failure label / notes: ____________________', '']
    (output / 'answer_review.md').write_text('\n'.join(packet))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
