"""Compare two chunkers and atomically replace local chunk snapshots offline."""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import tempfile

from filelock import FileLock
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ('fixed_overlap', 'heading_aware')
PARKS = {'yose': 'Yosemite', 'yell': 'Yellowstone', 'grca': 'Grand Canyon'}


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class Chunker:
    def __init__(self, config, root=ROOT):
        self.config = config
        path = Path(root) / config['tokenizer']['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != config['tokenizer']['sha256']:
            raise ValueError('Tokenizer checksum mismatch')
        self.tokenizer = Tokenizer.from_file(str(path))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        target, overlap = config['target_body_tokens'], config['fixed_overlap_tokens']
        if not 0 <= overlap < target or target < 32:
            raise ValueError('Require target >= 32 and 0 <= overlap < target')
        if not 1 <= config['maximum_prefix_tokens'] <= 64:
            raise ValueError('Require a heading-prefix budget between 1 and 64 tokens')
        if config['maximum_input_tokens'] > 256:
            raise ValueError('MiniLM input limit is 256 tokens')
        if target + config['maximum_prefix_tokens'] + 2 > config['maximum_input_tokens']:
            raise ValueError('Body, heading prefix, and special tokens exceed the input budget')

    def count(self, text, special=False):
        return len(self.tokenizer.encode(text, add_special_tokens=special).ids)

    def prefix(self, doc, heading_path):
        raw = ' > '.join([PARKS.get(doc['park'], doc['park'])] + (heading_path or [doc['title']]))
        encoding = self.tokenizer.encode(raw, add_special_tokens=False)
        limit = self.config['maximum_prefix_tokens']
        shortened = len(encoding.ids) > limit
        if shortened:
            # Only the embedding prefix is shortened; full headings remain in
            # metadata and original heading blocks are chunked as body content.
            raw = raw[:encoding.offsets[limit - 1][1]]
        return raw + '\n\n', ['heading_prefix_shortened'] if shortened else []

    def windows(self, text, budget, overlap=0):
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        offsets = encoded.offsets
        start = 0
        while start < len(offsets):
            end = min(start + budget, len(offsets))
            fragment = text[offsets[start][0]:offsets[end - 1][1]]
            # Retokenize substrings: a cut wordpiece can change its token count.
            while self.count(fragment) > budget and end > start + 1:
                end -= 1
                fragment = text[offsets[start][0]:offsets[end - 1][1]]
            if self.count(fragment) > budget:
                raise ValueError('A source fragment cannot fit the token budget')
            flags = []
            if encoded.tokens[start].startswith('##') or (end < len(offsets) and encoded.tokens[end].startswith('##')):
                flags.append('wordpiece_boundary_split')
            yield fragment, offsets[start][0], offsets[end - 1][1], flags
            if end == len(offsets):
                break
            start = max(start + 1, end - overlap)

    @staticmethod
    def block_ranges(doc):
        ranges, offset = [], 0
        for index, block in enumerate(doc['blocks']):
            ranges.append((index, offset, offset + len(block['text'])))
            offset += len(block['text']) + 2
        if '\n\n'.join(b['text'] for b in doc['blocks']) != doc['text']:
            raise ValueError('Normalized block text differs from document text')
        return ranges

    def make(self, doc, strategy, position, text, refs, heading_path, flags=(), table_refs=()):
        prefix, prefix_flags = self.prefix(doc, heading_path)
        embedding_input = prefix + text
        body_count, input_count = self.count(text), self.count(embedding_input, special=True)
        if body_count > self.config['target_body_tokens'] or input_count > self.config['maximum_input_tokens']:
            raise ValueError('Chunk exceeds token budget; no truncation permitted')
        identity = {'doc_id': doc['doc_id'], 'content_sha256': doc['content_sha256'],
                    'strategy': strategy, 'chunker_version': self.config['chunker_version'],
                    'parameters': self.config, 'position': position,
                    'input_sha256': hashlib.sha256(embedding_input.encode()).hexdigest()}
        pages = sorted({doc['blocks'][r['block_index']]['page_number'] for r in refs if 'page_number' in doc['blocks'][r['block_index']]})
        paths = list(dict.fromkeys(tuple(doc['blocks'][r['block_index']]['heading_path']) for r in refs))
        extra_flags = ['pdf_layout_requires_review'] if doc['page_type'] == 'pdf' else []
        return {'chunk_id': digest(identity), 'doc_id': doc['doc_id'], 'content_sha256': doc['content_sha256'],
                'strategy': strategy, 'chunker_version': self.config['chunker_version'], 'position': position,
                'text': text, 'embedding_input': embedding_input, 'body_tokens': body_count, 'input_tokens': input_count,
                'input_sha256': hashlib.sha256(embedding_input.encode()).hexdigest(),
                'source_fragments': refs, 'table_refs': list(table_refs), 'page_numbers': pages,
                'heading_path': heading_path, 'source_heading_paths': [list(p) for p in paths],
                'flags': sorted(set(list(flags) + prefix_flags + extra_flags)),
                'url': doc['url'], 'title': doc['title'], 'park': doc['park'], 'page_type': doc['page_type'],
                'crawl_timestamp': doc['crawl_timestamp'], 'raw_sha256': doc['raw_sha256'],
                'source_last_updated': doc.get('source_last_updated'), 'effective_date': doc.get('effective_date'),
                'coverage_period': doc.get('coverage_period'),
                'privacy_review_status': doc.get('privacy_review_status', 'pending'),
                'temporal_review_status': doc.get('temporal_review_status', 'pending'),
                'ready_for_index': doc.get('privacy_review_status') == 'approved' and doc.get('temporal_review_status') == 'approved'}

    def fixed(self, doc):
        ranges = self.block_ranges(doc)
        chunks = []
        for text, start, end, flags in self.windows(doc['text'], self.config['target_body_tokens'], self.config['fixed_overlap_tokens']):
            refs, tables = [], []
            for index, lo, hi in ranges:
                if lo < end and hi > start:
                    refs.append({'block_index': index, 'start': max(start, lo) - lo, 'end': min(end, hi) - lo})
                    block = doc['blocks'][index]
                    if block['type'] == 'table':
                        table_id = f"{doc['doc_id']}:table:{block['table_index']}"
                        tables.append({'table_id': table_id, 'table_index': block['table_index']})
                        row_start = lo
                        for row in block['text'].splitlines():
                            row_end = row_start + len(row)
                            if row_start < start < row_end or row_start < end < row_end:
                                flags.append('table_row_cut')
                            row_start = row_end + 1
            paths = [doc['blocks'][r['block_index']]['heading_path'] for r in refs]
            if len({tuple(p) for p in paths}) > 1:
                flags.append('crosses_heading_boundary')
            chunks.append(self.make(doc, 'fixed_overlap', len(chunks), text, refs, paths[0] if paths else [], flags, tables))
        return chunks

    def text_units(self, block, index):
        text = block['text']
        boundaries = list(re.finditer(r'(?<=[.!?])\s+(?=[A-Z0-9])', text))
        start = 0
        for boundary in boundaries + [None]:
            end = boundary.start() if boundary else len(text)
            piece = text[start:end]
            if piece.strip():
                for fragment, lo, hi, flags in self.windows(piece, self.config['target_body_tokens']):
                    if self.count(piece) > self.config['target_body_tokens']:
                        flags.append('oversized_sentence_split')
                    yield {'text': fragment, 'refs': [{'block_index': index, 'start': start + lo, 'end': start + hi}], 'flags': flags}
            start = boundary.end() if boundary else len(text)

    def table_chunks(self, doc, index, position):
        block = doc['blocks'][index]
        table_index = block['table_index']
        table = doc['tables'][table_index]
        rows = table['rows']
        row_texts = [' | '.join(row) for row in rows]
        if '\n'.join(row_texts) != block['text']:
            raise ValueError('Structured table rows differ from normalized table text')
        offsets, cursor = [], 0
        for row in row_texts:
            offsets.append((cursor, cursor + len(row)))
            cursor += len(row) + 1
        spans = table.get('cell_spans') or []
        header_count = 0
        for row_spans in spans:
            if row_spans and all(cell.get('header') for cell in row_spans):
                header_count += 1
            else:
                break
        flags = []
        if not header_count:
            header_count = 1
            flags.append('table_header_inferred')
        header_count = min(header_count, len(rows))
        header = '\n'.join(row_texts[:header_count])
        header_refs = [{'block_index': index, 'start': offsets[r][0], 'end': offsets[r][1]} for r in range(header_count)]
        table_id = f"{doc['doc_id']}:table:{table_index}"
        chunks = []

        def emit(text, refs, row_indices, extra=()):
            chunks.append(self.make(doc, 'heading_aware', position + len(chunks), text, refs,
                                    block['heading_path'], flags + list(extra),
                                    [{'table_id': table_id, 'table_index': table_index, 'row_indices': row_indices,
                                      'header_row_indices': list(range(header_count)), 'full_row_reference_available': True}]))

        repeat_header = self.count(header) <= self.config['target_body_tokens'] // 2
        if not repeat_header:
            flags.append('table_headers_separate')
            for fragment, lo, hi, cut_flags in self.windows(header, self.config['target_body_tokens']):
                emit(fragment, [{'block_index': index, 'start': lo, 'end': hi}], list(range(header_count)), cut_flags)
        pending_rows = []

        def flush():
            if pending_rows:
                indices = (list(range(header_count)) if repeat_header else []) + pending_rows
                emit('\n'.join(row_texts[r] for r in indices),
                     [{'block_index': index, 'start': offsets[r][0], 'end': offsets[r][1]} for r in indices], list(pending_rows))
                pending_rows.clear()

        for r in range(header_count, len(rows)):
            proposed = (header + '\n' if repeat_header else '') + '\n'.join(row_texts[n] for n in pending_rows + [r])
            if self.count(proposed) <= self.config['target_body_tokens']:
                pending_rows.append(r)
                continue
            flush()
            proposed = (header + '\n' if repeat_header else '') + row_texts[r]
            if self.count(proposed) <= self.config['target_body_tokens']:
                pending_rows.append(r)
                continue
            # Oversized rows are split at cell boundaries, with cell-number labels.
            # The original row/table remains available through explicit references.
            cell_start = offsets[r][0]
            prefix = header + '\n' if repeat_header else ''
            budget = self.config['target_body_tokens'] - self.count(prefix) - 12
            for cell_index, cell in enumerate(rows[r]):
                for fragment, lo, hi, cut_flags in self.windows(cell, budget):
                    label = f'Row {r + 1}, Cell {cell_index + 1}: '
                    emit(prefix + label + fragment,
                         (header_refs if repeat_header else []) + [{'block_index': index, 'start': cell_start + lo, 'end': cell_start + hi}],
                         [r], ['oversized_row_split', 'table_cell_label'] + cut_flags)
                cell_start += len(cell) + 3
        flush()
        if header_count == len(rows) and repeat_header:
            emit(header, header_refs, list(range(header_count)))
        return chunks

    def structured(self, doc):
        self.block_ranges(doc)
        chunks, pending = [], []
        scope = None

        def flush():
            if pending:
                refs = [r for unit in pending for r in unit['refs']]
                flags = [flag for unit in pending for flag in unit['flags']]
                chunks.append(self.make(doc, 'heading_aware', len(chunks), '\n\n'.join(u['text'] for u in pending), refs,
                                        doc['blocks'][refs[0]['block_index']]['heading_path'], flags))
                pending.clear()

        for index, block in enumerate(doc['blocks']):
            current = (tuple(block['heading_path']), block.get('page_number'))
            if scope != current:
                flush()
                scope = current
            if block['type'] == 'table':
                flush()
                chunks.extend(self.table_chunks(doc, index, len(chunks)))
                continue
            for unit in self.text_units(block, index):
                candidate = '\n\n'.join(u['text'] for u in pending + [unit])
                if self.count(candidate) > self.config['target_body_tokens']:
                    flush()
                pending.append(unit)
        flush()
        return chunks

    def validate(self, doc, chunks):
        coverage = [bytearray(len(b['text'])) for b in doc['blocks']]
        for chunk in chunks:
            if self.count(chunk['embedding_input'], special=True) != chunk['input_tokens']:
                raise ValueError('Recorded token count mismatch')
            if chunk['input_tokens'] > self.config['maximum_input_tokens']:
                raise ValueError('Embedding would truncate')
            for ref in chunk['source_fragments']:
                index, start, end = ref['block_index'], ref['start'], ref['end']
                if not 0 <= start <= end <= len(coverage[index]):
                    raise ValueError('Invalid source fragment')
                coverage[index][start:end] = b'\x01' * (end - start)
        for index, block in enumerate(doc['blocks']):
            for offset, char in enumerate(block['text']):
                # Table pipes are serialization delimiters, not cell content.
                if not char.isspace() and not (block['type'] == 'table' and char == '|') and not coverage[index][offset]:
                    # BERT deliberately removes control characters during normalization.
                    if self.count(char) == 0:
                        continue
                    raise ValueError(f"Uncovered source character in {doc['url']} block {index} offset {offset}")


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {'count': 0}
    bins = {'1-50': 0, '51-100': 0, '101-150': 0, '151-200': 0, '201-256': 0}
    for value in ordered:
        label = '1-50' if value <= 50 else '51-100' if value <= 100 else '101-150' if value <= 150 else '151-200' if value <= 200 else '201-256'
        bins[label] += 1
    return {'count': len(ordered), 'min': ordered[0], 'median': statistics.median(ordered),
            'p95': ordered[math.ceil(len(ordered) * .95) - 1], 'max': ordered[-1], 'histogram': bins}


def validate_snapshot(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    all_ids = set()
    for strategy in STRATEGIES:
        path = directory / f'{strategy}.jsonl'
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['files'][strategy]['sha256']:
            raise ValueError('Snapshot file checksum mismatch')
        chunks = [json.loads(line) for line in path.read_text().splitlines()]
        ids = [c['chunk_id'] for c in chunks]
        if ids != manifest['files'][strategy]['chunk_ids'] or len(set(ids)) != len(ids):
            raise ValueError('Snapshot IDs do not match manifest')
        if all_ids.intersection(ids):
            raise ValueError('Cross-strategy ID collision')
        all_ids.update(ids)
    return all_ids


def _build_snapshot(documents_path, config, output, root=ROOT):
    output = Path(output)
    versions = output / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    documents = [json.loads(line) for line in Path(documents_path).read_text().splitlines()]
    documents.sort(key=lambda d: d['doc_id'])
    if not documents or len({d['doc_id'] for d in documents}) != len(documents):
        raise ValueError('Corpus must contain unique nonempty documents')
    for doc in documents:
        if hashlib.sha256(doc['text'].encode()).hexdigest() != doc['content_sha256']:
            raise ValueError('Normalized content hash mismatch')
    corpus_identity = [{'doc_id': d['doc_id'], 'content_sha256': d['content_sha256'],
                        'normalized_document_sha256': digest(d)} for d in documents]
    version = digest({'corpus': corpus_identity, 'config': config})
    pointer = output / 'active.json'
    previous = json.loads(pointer.read_text())['version'] if pointer.exists() else None
    previous_ids = validate_snapshot(versions / previous) if previous else set()
    for stale in versions.glob('.building-*'):
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale)
    temporary = Path(tempfile.mkdtemp(prefix='.building-', dir=versions))
    destination = versions / version
    manifest = {'version': version, 'corpus_fingerprint': digest(corpus_identity),
                'document_count': len(documents), 'config': config, 'files': {}}
    summary = {'version': version, 'corpus_fingerprint': manifest['corpus_fingerprint'],
               'document_count': len(documents), 'config': config, 'strategies': {}}
    created_destination = False
    try:
        chunker = Chunker(config, root)
        for strategy in STRATEGIES:
            chunks = []
            for doc in documents:
                items = chunker.fixed(doc) if strategy == 'fixed_overlap' else chunker.structured(doc)
                if not items:
                    raise ValueError('Document produced no chunks')
                chunker.validate(doc, items)
                chunks.extend(items)
            path = temporary / f'{strategy}.jsonl'
            path.write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in chunks))
            manifest['files'][strategy] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'chunk_ids': [c['chunk_id'] for c in chunks]}
            summary['strategies'][strategy] = {'chunk_count': len(chunks), 'body_tokens': distribution([c['body_tokens'] for c in chunks]),
                                                'full_input_tokens': distribution([c['input_tokens'] for c in chunks]),
                                                'flags': dict(Counter(flag for c in chunks for flag in c['flags'])),
                                                'by_page_type': dict(Counter(c['page_type'] for c in chunks)),
                                                'by_park': dict(Counter(c['park'] for c in chunks)),
                                                'ready_for_index': sum(c['ready_for_index'] for c in chunks),
                                                'source_character_coverage_verified': True}
        (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        (temporary / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
        new_ids = validate_snapshot(temporary)
        if destination.exists():
            if validate_snapshot(destination) != new_ids or (destination / 'manifest.json').read_bytes() != (temporary / 'manifest.json').read_bytes():
                raise ValueError('Existing version differs from rebuilt snapshot')
            shutil.rmtree(temporary)
        else:
            os.replace(temporary, destination)
            created_destination = True
        pointer_tmp = output / 'active.json.tmp'
        pointer_tmp.write_text(json.dumps({'version': version}, indent=2) + '\n')
        os.replace(pointer_tmp, pointer)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        current = json.loads(pointer.read_text())['version'] if pointer.exists() else None
        if created_destination and current != version and destination.exists():
            shutil.rmtree(destination)
        (output / 'active.json.tmp').unlink(missing_ok=True)
        raise
    removed_versions = []
    for old in versions.iterdir():
        if old != destination and old.is_dir() and not old.is_symlink() and re.fullmatch('[a-f0-9]{64}', old.name):
            shutil.rmtree(old)
            removed_versions.append(old.name)
    active_ids = validate_snapshot(destination)
    if active_ids != new_ids or any(p != destination for p in versions.iterdir()):
        raise ValueError('Stale chunk snapshot remains')
    summary['migration'] = {'previous_version': previous, 'active_version': version,
                            'removed_versions': sorted(removed_versions), 'old_only_ids_removed': len(previous_ids - active_ids),
                            'active_id_set_verified': True, 'orphan_versions': 0,
                            'scope': 'Local chunk files only; vector/BM25 index migration awaits retrieval implementation.'}
    return summary


def build_snapshot(documents_path, config, output, root=ROOT):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(output / '.build.lock'):
        return _build_snapshot(documents_path, config, output, root)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--documents', type=Path, default=ROOT / 'data/normalized/documents.jsonl')
    p.add_argument('--config', type=Path, default=ROOT / 'config/chunking.json')
    p.add_argument('--output-dir', type=Path, default=ROOT / 'data/chunks')
    p.add_argument('--summary', type=Path, default=ROOT / 'data/audit/chunking_summary.json')
    p.add_argument('--target-tokens', type=int)
    p.add_argument('--overlap-tokens', type=int)
    args = p.parse_args()
    config = json.loads(args.config.read_text())
    if args.target_tokens is not None:
        config['target_body_tokens'] = args.target_tokens
    if args.overlap_tokens is not None:
        config['fixed_overlap_tokens'] = args.overlap_tokens
    summary = build_snapshot(args.documents, config, args.output_dir)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2) + '\n')
    history = args.summary.parent / 'chunk_runs'
    history.mkdir(exist_ok=True)
    (history / (summary['version'] + '.json')).write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
