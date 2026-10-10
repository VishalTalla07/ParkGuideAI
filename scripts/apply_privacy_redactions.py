"""Apply locally reviewed exact redactions consistently, without crawling."""
import hashlib
import json
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]


def transform(value, replacements):
    if isinstance(value, str):
        for original, replacement in replacements:
            value = value.replace(original, replacement)
        return value
    if isinstance(value, list):
        return [transform(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: transform(v, replacements) for k, v in value.items()}
    return value


def main():
    source = ROOT / 'data/normalized/documents.jsonl'
    rules = json.loads((ROOT / 'data/review/privacy_redaction_rules.json').read_text())
    docs = list(map(json.loads, source.read_text().splitlines()))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    changes = []
    for i, d in enumerate(docs):
        selected = [(r['original'], r['replacement']) for r in rules if r['url'] == d['url']]
        updated = transform(d, selected)
        if updated == d:
            continue
        updated['text'] = '\n\n'.join(b['text'] for b in updated['blocks'])
        updated['content_sha256'] = hashlib.sha256(updated['text'].encode()).hexdigest()
        updated['word_count'] = len(updated['text'].split())
        updated['privacy_review_status'] = 'pending'
        updated['privacy_redaction_version'] = 'exact-directory-name-v1'
        assert all(original not in json.dumps(updated) for original, _ in selected)
        docs[i] = updated
        changes.append({'doc_id': d['doc_id'], 'url': d['url'],
                        'old_normalized_sha256': hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                        'new_normalized_sha256': hashlib.sha256(json.dumps(updated, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
    if changes:
        temporary = source.with_suffix('.tmp')
        temporary.write_text(''.join(json.dumps(d, ensure_ascii=False) + '\n' for d in docs))
        os.replace(temporary, source)
    queue_path = ROOT / 'data/review/relevance_queue.json'
    queue = json.loads(queue_path.read_text())
    for change in changes:
        r = next(r for r in queue['decisions'] if r['doc_id'] == change['doc_id'])
        assert r['normalized_document_sha256'] == change['old_normalized_sha256']
        r['prior_normalized_sha256'] = r['normalized_document_sha256']
        r['normalized_document_sha256'] = change['new_normalized_sha256']
        r['privacy_only_revision_note'] = 'Exact directory name minimization; prior user relevance acceptance retained. No substantive visitor information changed.'
    queue_path.write_text(json.dumps(queue, indent=2) + '\n')
    receipt = {'documents_changed': len(changes), 'changes': changes,
               'corpus_sha256_before': before, 'corpus_sha256_after': hashlib.sha256(source.read_bytes()).hexdigest(),
               'public_organizations_and_business_phone_numbers': 'preserved',
               'privacy_approval': 'pending final human acceptance'}
    (ROOT / 'data/review/privacy_redaction_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print('Changed documents:', len(changes))


if __name__ == '__main__':
    main()
