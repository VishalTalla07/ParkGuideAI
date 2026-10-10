from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('extractor', Path(__file__).resolve().parents[1] / 'scripts/extract_sources.py')
e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e)


def record(body, url='https://www.nps.gov/yose/planyourvisit/test.htm'):
    return {'url': url, 'sha256': hashlib.sha256(body).hexdigest(), 'headers': {'Content-Type': 'text/html'},
            'crawl_timestamp': '2026-10-09T18:00:00+00:00', 'raw_path': 'blobs/test', 'purpose': 'document', 'status': 200}


def rotated_pdf():
    stream = b'BT /F1 14 Tf 40 700 Td (Visitor Guide) Tj 0 -24 Td (September 23 - November 24, 2026) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Rotate 180 /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    body, offsets = b'%PDF-1.4\n', [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += str(index).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(body)
    body += b'xref\n0 6\n0000000000 65535 f \n'
    body += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    return body + b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'


class ExtractionTests(unittest.TestCase):
    def test_rotated_pdf_reading_order_and_coverage(self):
        body = rotated_pdf()
        metadata = record(body, 'https://www.nps.gov/yose/planyourvisit/guide.pdf')
        metadata['headers']['Content-Type'] = 'application/pdf'
        doc = e.normalize(metadata, body)
        self.assertIn('Visitor Guide', doc['text'])
        self.assertNotIn('ediuG', doc['text'])
        self.assertEqual(doc['coverage_period']['start'], '2026-09-23')
        self.assertEqual(doc['coverage_period']['end'], '2026-11-24')
        self.assertEqual(doc['blocks'][0]['page_number'], 1)
        self.assertIsNone(doc['effective_date'])

    def test_headings_tables_and_boilerplate(self):
        body = b'''<title>Camping</title><nav>Outside navigation</nav><main>
        <div class="FourthLevelNav"><p>Related navigation</p></div><h1>Camping</h1>
        <h2>Fees</h2><p>Reserve a campsite.</p>
        <table class="CS_Layout_Table"><tr><td><table><tr><th>Site</th><th>Fee</th></tr>
        <tr><td rowspan="2">Tent</td><td>$30</td></tr><tr><td>$20</td></tr></table></td></tr></table>
        <footer>Unwanted footer</footer><script>Unwanted script</script>
        <p>Last updated: September 28, 2026</p></main>'''
        doc = e.normalize(record(body), body)
        self.assertNotIn('navigation', doc['text'])
        self.assertNotIn('Unwanted', doc['text'])
        self.assertEqual(doc['tables'][0]['rows'][1], ['Tent', '$30'])
        self.assertEqual(doc['tables'][0]['cell_spans'][1][0]['rowspan'], '2')
        paragraph = next(b for b in doc['blocks'] if b['type'] == 'paragraph')
        self.assertEqual(paragraph['heading_path'], ['Camping', 'Fees'])
        self.assertEqual(doc['source_last_updated'], '2026-09-28')
        self.assertIsNone(doc['effective_date'])

    def test_contact_redaction_without_price_date_damage(self):
        counts = Counter()
        text = e.redact('a.person@example.org (928)638-7888 209-372-0200 209/372-0200 123-45-6789 $30 2026-2027', counts)
        self.assertNotIn('@', text)
        self.assertNotIn('638-7888', text)
        self.assertIn('$30 2026-2027', text)
        self.assertEqual(counts, {'email': 1, 'phone': 3, 'ssn': 1})

    def test_fee_accordion_keeps_category_price_but_removes_generic_buttons(self):
        body = b'''<main><h1>Fees</h1><h2>For Vehicles</h2>
        <button><div class="FeesPassesFeesItem__HeaderText">Private Vehicle
        <div class="FeesPassesFeesItem__Cost">$35.00</div></div></button>
        <p>Valid for seven days.</p><button>Unwanted interface control</button></main>'''
        doc = e.extract_html(body)
        texts = [b['text'] for b in doc['blocks']]
        self.assertIn('Private Vehicle $35.00', texts)
        self.assertIn('Valid for seven days.', texts)
        self.assertNotIn('Unwanted interface control', texts)
        fee = next(b for b in doc['blocks'] if b['text'] == 'Private Vehicle $35.00')
        self.assertEqual(fee['heading_path'], ['Fees', 'For Vehicles'])

    def test_missing_main_requires_review(self):
        with self.assertRaises(ValueError):
            e.extract_html(b'<p>Navigation-only page</p>')

    def test_hash_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            e.normalize(record(b'original'), b'changed')

    def test_out_of_scope_event_excluded(self):
        self.assertIsNotNone(e.exclusion_reason('https://www.nps.gov/grca/planyourvisit/2019-film-festival.htm', 'Festival'))
        self.assertIsNone(e.exclusion_reason('https://www.nps.gov/yose/planyourvisit/camping.htm', 'Camping'))
        self.assertIsNotNone(e.exclusion_reason('https://www.nps.gov/yell/planyourvisit/yellowstonepledge-french.htm', 'French Pledge'))
        self.assertEqual(e.exclusion_reason('https://www.nps.gov/yose/planyourvisit/ymcstaff.htm', 'Clinic Staff'), 'personal_directory')

    def test_duplicates_and_rebuild_without_recrawl(self):
        body = ('<main><h1>Camp</h1><p>' + 'Useful visitor information. ' * 30 + '</p></main>').encode()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = base / 'raw'
            (raw / 'blobs').mkdir(parents=True)
            (raw / 'blobs/test').write_bytes(body)
            records = [record(body), record(body, 'https://www.nps.gov/yose/planyourvisit/copy.htm')]
            (raw / 'manifest.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
            output = base / 'normalized'
            summary = e.extract_all(raw, output, base / 'summary.json')
            self.assertEqual(summary['documents'], 1)
            self.assertEqual(summary['exclusions']['normalized_duplicate'], 1)
            before = (output / 'documents.jsonl').read_bytes()
            e.extract_all(raw, output, base / 'summary.json')
            self.assertEqual(before, (output / 'documents.jsonl').read_bytes())
            self.assertTrue((output / 'review_inventory.csv').exists())


if __name__ == '__main__':
    unittest.main()
