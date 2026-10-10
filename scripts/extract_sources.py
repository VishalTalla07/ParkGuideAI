"""Normalize saved NPS responses; never makes network requests."""
import argparse
import csv
from collections import Counter
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re

from bs4 import BeautifulSoup
import pdfplumber
from pypdf import PdfReader, PdfWriter

ROOT = Path(__file__).resolve().parents[1]
EXTRACTION_VERSION = 'nps-v1'
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
PHONE = re.compile(r'(?<!\d)(?:\+?1[ /.-]?)?(?:\(\d{3}\)[ /.-]?|\d{3}[ /.-])\d{3}[ /.-]\d{4}(?!\d)')
SSN = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def redact(value, counts):
    for label, pattern in [('email', EMAIL), ('phone', PHONE), ('ssn', SSN)]:
        value, n = pattern.subn(f'[REDACTED_{label.upper()}]', value)
        counts[label] += n
    return value


def exact_redactions(value, replacements):
    if isinstance(value, str):
        for original, replacement in replacements:
            value = value.replace(original, replacement)
        return value
    if isinstance(value, list):
        return [exact_redactions(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: exact_redactions(item, replacements) for key, item in value.items()}
    return value


def layout_table(tag):
    return ('layout' in ' '.join(tag.get('class', [])).lower()
            or tag.get('id', '').lower().startswith('cs_idlayout')
            or tag.get('role') == 'presentation')


def extract_html(body):
    soup = BeautifulSoup(body, 'lxml')
    page_title = clean(soup.title.get_text(' ', strip=True)) if soup.title else ''
    main = soup.select_one('#main') or soup.find('main') or soup.select_one('[role="main"]')
    if main is None:
        raise ValueError('Missing main content landmark; manual extraction required')
    # NPS puts entrance categories and prices inside accordion buttons. Preserve
    # those source facts as paragraphs before removing generic UI controls.
    for label in list(main.select('.FeesPassesFeesItem__HeaderText')):
        button = label.find_parent('button')
        if button is not None:
            paragraph = soup.new_tag('p')
            paragraph.string = clean(label.get_text(' ', strip=True))
            button.replace_with(paragraph)
    for tag in list(main.select('script,style,nav,footer,header,aside,iframe,svg,form,button,.modal,.FourthLevelNav,.ParkFooter,.usa-breadcrumb,#adminTools')):
        if tag.parent is not None:
            tag.decompose()
    # Unwrap CommonSpot layout scaffolding while preserving embedded data tables.
    for table in list(main.find_all('table')):
        if layout_table(table):
            table.unwrap()
    title_tag = main.find('h1')
    title = clean(title_tag.get_text(' ', strip=True)) if title_tag else page_title
    dates = []
    for text in main.stripped_strings:
        match = re.search(r'Last updated:\s*([A-Za-z]+ \d{1,2}, \d{4})', text)
        if match:
            try:
                dates.append(datetime.strptime(match[1], '%B %d, %Y').date().isoformat())
            except ValueError:
                pass
    path, blocks, tables = [], [], []
    for tag in main.find_all(re.compile(r'^h[1-6]$|^p$|^li$|^table$')):
        if tag.find_parent('table') or tag.find_parent(['p', 'li']):
            continue
        text = clean(tag.get_text(' ', strip=True))
        if not text or text.startswith('Last updated:'):
            continue
        if re.fullmatch(r'h[1-6]', tag.name):
            level = int(tag.name[1])
            path = [item for item in path if item[0] < level] + [(level, text)]
            blocks.append({'type': 'heading', 'heading_path': [x[1] for x in path], 'text': text})
        elif tag.name == 'table':
            rows = []
            spans = []
            for tr in tag.find_all('tr'):
                if tr.find_parent('table') != tag:
                    continue
                cells = tr.find_all(['th', 'td'], recursive=False)
                if cells:
                    rows.append([clean(cell.get_text(' ', strip=True)) for cell in cells])
                    spans.append([{'rowspan': cell.get('rowspan', '1'), 'colspan': cell.get('colspan', '1'), 'header': cell.name == 'th'} for cell in cells])
            if len(rows) < 2 or not any(len(row) >= 2 for row in rows):
                continue
            caption = tag.find('caption')
            table = {'heading_path': [x[1] for x in path], 'caption': clean(caption.get_text(' ', strip=True)) if caption else '', 'rows': rows, 'cell_spans': spans}
            tables.append(table)
            blocks.append({'type': 'table', 'heading_path': table['heading_path'], 'table_index': len(tables) - 1, 'text': '\n'.join(' | '.join(row) for row in rows)})
        else:
            blocks.append({'type': 'list_item' if tag.name == 'li' else 'paragraph', 'heading_path': [x[1] for x in path], 'text': text})
    return {'title': title, 'blocks': blocks, 'tables': tables, 'declared_language': soup.html.get('lang') if soup.html else None,
            'source_last_updated': dates[0] if len(set(dates)) == 1 else None,
            'effective_date': None, 'effective_date_note': 'A page update date is not a policy effective date.'}


def extract_pdf(body):
    blocks, tables = [], []
    reader = PdfReader(io.BytesIO(body))
    buffer = io.BytesIO(body)
    if any(page.rotation for page in reader.pages):
        writer = PdfWriter()
        for page in reader.pages:
            # Page rotation makes pdfminer's backwards glyph positions split
            # words. Normalize only the in-memory parsing copy, never raw bytes.
            page.rotation = 0
            writer.add_page(page)
        if reader.metadata:
            writer.add_metadata({k: str(v) for k, v in reader.metadata.items() if v is not None})
        buffer = io.BytesIO()
        writer.write(buffer)
        buffer.seek(0)
    with pdfplumber.open(buffer) as pdf:
        metadata = pdf.metadata or {}
        title = clean(str(metadata.get('Title') or ''))
        for page in pdf.pages:
            # Booklet covers mix upright and upside-down panels on one PDF page.
            # Read each orientation with its correct glyph direction, but render
            # English text left-to-right. This prevents reversed or spaced words.
            def inverted(obj):
                matrix = obj.get('matrix', (1, 0, 0, 1))
                return matrix[0] < 0 and matrix[3] < 0
            orientations = list(dict.fromkeys(inverted(char) for char in page.chars))
            for orientation in orientations:
                panel = page.filter(lambda obj: obj.get('object_type') == 'char' and inverted(obj) == orientation)
                text = panel.extract_text(layout=False, use_text_flow=True,
                                          char_dir='rtl' if orientation else 'ltr',
                                          char_dir_render='ltr', line_dir_render='ttb') or ''
                if text.strip():
                    blocks.append({'type': 'pdf_page', 'heading_path': [f'Page {page.page_number}'], 'page_number': page.page_number,
                                   'text_orientation': 180 if orientation else 0, 'text': text.strip()})
            for rows in page.extract_tables():
                rows = [[clean(cell) for cell in row] for row in rows]
                if len(rows) >= 2 and any(len(row) >= 2 for row in rows):
                    tables.append({'heading_path': [f'Page {page.page_number}'], 'page_number': page.page_number, 'caption': '', 'rows': rows, 'cell_spans': None})
        coverage = None
        if blocks:
            cover_text = '\n'.join(b['text'] for b in blocks if b['page_number'] == 1)
            match = re.search(r'([A-Z][a-z]+ \d{1,2})\s*[–−-]\s*([A-Z][a-z]+ \d{1,2}),\s*(20\d{2})', cover_text)
            if match:
                try:
                    start = datetime.strptime(match[1] + ', ' + match[3], '%B %d, %Y').date()
                    end = datetime.strptime(match[2] + ', ' + match[3], '%B %d, %Y').date()
                    if start <= end:
                        coverage = {'start': start.isoformat(), 'end': end.isoformat(), 'basis': 'explicit cover date range'}
                except ValueError:
                    pass
        return {'title': title, 'blocks': blocks, 'tables': tables, 'pdf_pages': len(pdf.pages), 'coverage_period': coverage,
                'source_last_updated': None, 'effective_date': None,
                'effective_date_note': 'PDF coverage dates require review; metadata creation date is not treated as an effective date.'}


def exclusion_reason(url, title):
    text = (url + ' ' + title).lower()
    if any(word in url.lower().rsplit('/', 1)[-1] for word in ('staff', 'directory', 'login')) or any(word in text for word in ('employee-directory', 'personnel-directory', 'visitor-profile')):
        return 'personal_directory'
    if re.search(r'(?:19|20)\d{2}', url) and any(word in text for word in ('festival', 'symposium', 'celebration', 'demonstrator', 'bugflow', 'rededication', 'geofest')):
        return 'dated_event_outside_visitor_planning_scope'
    languages = 'arabic chinese czech dutch filipino french german hebrew hindi hmong hungarian indonesian italian japanese korean lithuanian polish portuguese romanian russian slovak spanish swahili swedish thai turkish ukrainian vietnamese'.split()
    if any(re.search(r'[-_/]' + lang + r'(?:[._/-]|$)', url.lower()) for lang in languages):
        return 'translated_page_outside_initial_english_corpus'
    if title.lower().startswith(('page not found', 'access denied', '404')):
        return 'error_page'
    return None


def normalize(record, body):
    if hashlib.sha256(body).hexdigest() != record['sha256']:
        raise ValueError('Raw SHA-256 mismatch')
    mime = next((v for k, v in record['headers'].items() if k.lower() == 'content-type'), '').lower()
    if 'application/pdf' in mime and body.startswith(b'%PDF-'):
        result, page_type = extract_pdf(body), 'pdf'
    elif 'text/html' in mime:
        result, page_type = extract_html(body), 'html'
    else:
        raise ValueError('Unsupported response type')
    # Local reviewed overlays prevent removed directory names returning on extraction.
    rules_path = ROOT / 'data/review/privacy_redaction_rules.json'
    if rules_path.exists():
        replacements = [(rule['original'], rule['replacement'])
                        for rule in json.loads(rules_path.read_text())
                        if rule['url'] == record['url']]
        result = exact_redactions(result, replacements)
    counts = Counter()
    result['title'] = redact(result['title'], counts) or record['url'].rsplit('/', 1)[-1]
    for block in result['blocks']:
        block['text'] = redact(block['text'], counts)
        block['heading_path'] = [redact(h, counts) for h in block['heading_path']]
    for table in result['tables']:
        table['caption'] = redact(table['caption'], counts)
        table['heading_path'] = [redact(h, counts) for h in table['heading_path']]
        table['rows'] = [[redact(cell, counts) for cell in row] for row in table['rows']]
    text = '\n\n'.join(block['text'] for block in result['blocks'])
    result.update({'doc_id': hashlib.sha256(record['url'].encode()).hexdigest(),
                   'url': record['url'], 'park': record['url'].split('/')[3],
                   'page_type': page_type, 'crawl_timestamp': record['crawl_timestamp'],
                   'raw_sha256': record['sha256'], 'raw_path': record['raw_path'],
                   'extraction_version': EXTRACTION_VERSION, 'text': text,
                   'content_sha256': hashlib.sha256(text.encode()).hexdigest(),
                   'redaction_occurrences': dict(counts), 'privacy_review_status': 'pending',
                   'temporal_review_status': 'pending', 'word_count': len(text.split())})
    return result


def extract_all(raw, output, summary_path):
    raw, output = Path(raw), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    latest = {}
    for line in (raw / 'manifest.jsonl').read_text().splitlines():
        record = json.loads(line)
        if record['purpose'] == 'document':
            latest[record['url']] = record
    documents, exclusions, hashes = [], [], {}
    for record in latest.values():
        url = record['url']
        if record.get('excluded_reason'):
            exclusions.append({'url': url, 'reason': record['excluded_reason']})
            continue
        if record['status'] != 200:
            exclusions.append({'url': url, 'reason': f"http_{record['status']}"})
            continue
        reason = exclusion_reason(url, '')
        if reason:
            exclusions.append({'url': url, 'reason': reason})
            continue
        try:
            doc = normalize(record, (raw / record['raw_path']).read_bytes())
        except Exception as exc:
            exclusions.append({'url': url, 'reason': 'extraction_failed', 'detail': str(exc)[:300]})
            continue
        reason = exclusion_reason(url, doc['title'])
        if reason or doc['word_count'] < 40:
            exclusions.append({'url': url, 'reason': reason or 'insufficient_text_manual_review'})
            continue
        digest = doc['content_sha256']
        if digest in hashes:
            exclusions.append({'url': url, 'reason': 'normalized_duplicate', 'duplicate_of': hashes[digest]})
            continue
        hashes[digest] = url
        documents.append(doc)
        print(f"EXTRACT {doc['page_type']} {doc['word_count']} words {url}", flush=True)
    for name, records in [('documents.jsonl', documents), ('exclusions.jsonl', exclusions)]:
        temporary = output / (name + '.tmp')
        temporary.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
        os.replace(temporary, output / name)
    # Review decisions live separately, so subsequent extraction cannot overwrite them.
    with (output / 'review_inventory.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['doc_id', 'url', 'title', 'park', 'page_type', 'word_count', 'table_count', 'source_last_updated', 'privacy_review_status', 'temporal_review_status'])
        writer.writeheader()
        for doc in documents:
            writer.writerow({key: len(doc['tables']) if key == 'table_count' else doc.get(key) for key in writer.fieldnames})
    types = Counter(d['page_type'] for d in documents)
    summary = {'extraction_version': EXTRACTION_VERSION, 'documents': len(documents),
               'by_page_type': dict(types), 'by_park': dict(Counter(d['park'] for d in documents)),
               'html_with_data_table_candidates': sum(d['page_type'] == 'html' and bool(d['tables']) for d in documents),
               'exclusions': dict(Counter(e['reason'] for e in exclusions)),
               'redaction_occurrences': dict(sum((Counter(d['redaction_occurrences']) for d in documents), Counter())),
               'review_status': 'Automated extraction only. Human privacy, relevance, table fidelity, and temporal review required before indexing.'}
    Path(summary_path).write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir', type=Path, default=ROOT / 'data/raw')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data/normalized')
    parser.add_argument('--summary', type=Path, default=ROOT / 'data/audit/extraction_summary.json')
    args = parser.parse_args()
    print(json.dumps(extract_all(args.raw_dir, args.output_dir, args.summary), indent=2))


if __name__ == '__main__':
    main()
