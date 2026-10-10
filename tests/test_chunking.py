import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('chunks', ROOT / 'scripts/chunk_sources.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def document(blocks, tables=None):
    text = '\n\n'.join(b['text'] for b in blocks)
    return {'doc_id': hashlib.sha256(b'https://www.nps.gov/yose/planyourvisit/test.htm').hexdigest(),
            'content_sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text,
            'blocks': blocks, 'tables': tables or [], 'url': 'https://www.nps.gov/yose/planyourvisit/test.htm',
            'title': 'Camping', 'park': 'yose', 'page_type': 'html',
            'crawl_timestamp': '2026-10-09T18:00:00+00:00', 'raw_sha256': 'a' * 64,
            'privacy_review_status': 'pending', 'temporal_review_status': 'pending'}


def paragraph(text, heading='Camping'):
    return {'type': 'paragraph', 'heading_path': [heading], 'text': text}


class ChunkingTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'config/chunking.json').read_text())
        self.config.update(target_body_tokens=48, fixed_overlap_tokens=8)
        self.chunker = c.Chunker(self.config)

    def test_budget_coverage_and_overlap(self):
        doc = document([paragraph('A reservation is required for camping. ' * 60)])
        fixed = self.chunker.fixed(doc)
        structured = self.chunker.structured(doc)
        self.assertGreater(len(fixed), 1)
        for chunks in (fixed, structured):
            self.chunker.validate(doc, chunks)
            self.assertTrue(all(x['body_tokens'] <= 48 and x['input_tokens'] <= 256 for x in chunks))
            self.assertTrue(all(not x['ready_for_index'] for x in chunks))
        self.assertLess(fixed[1]['source_fragments'][0]['start'], fixed[0]['source_fragments'][0]['end'])

    def test_structure_stays_under_one_heading(self):
        doc = document([paragraph('Fees apply. Reserve online.', 'Fees'), paragraph('Dogs must be leashed.', 'Pets')])
        structured = self.chunker.structured(doc)
        self.assertEqual(len(structured), 2)
        self.assertEqual(structured[0]['heading_path'], ['Fees'])
        self.assertEqual(structured[1]['heading_path'], ['Pets'])
        self.assertNotIn('Pets', structured[0]['text'])

    def test_headers_repeat_and_complete_rows_are_preserved(self):
        rows = [['Campground', 'Fee']] + [[f'Campsite {n}', '$30 for one night'] for n in range(18)]
        table = {'rows': rows, 'cell_spans': [[{'header': True}, {'header': True}]] + [[{'header': False}, {'header': False}] for _ in rows[1:]]}
        block = {'type': 'table', 'heading_path': ['Camping'], 'table_index': 0, 'text': '\n'.join(' | '.join(row) for row in rows)}
        doc = document([block], [table])
        chunks = self.chunker.structured(doc)
        self.assertGreater(len(chunks), 1)
        self.chunker.validate(doc, chunks)
        self.assertTrue(all(x['text'].startswith('Campground | Fee\n') for x in chunks))
        covered = [r for x in chunks for r in x['table_refs'][0]['row_indices']]
        self.assertEqual(covered, list(range(1, len(rows))))
        self.assertTrue(all('oversized_row_split' not in x['flags'] for x in chunks))

    def test_oversized_row_splits_cells_with_full_reference(self):
        rows = [['Site', 'Details'], ['Tent', 'Reservation information is available online. ' * 70]]
        block = {'type': 'table', 'heading_path': ['Camping'], 'table_index': 0, 'text': '\n'.join(' | '.join(row) for row in rows)}
        doc = document([block], [{'rows': rows, 'cell_spans': None}])
        chunks = self.chunker.structured(doc)
        self.chunker.validate(doc, chunks)
        self.assertTrue(any('oversized_row_split' in x['flags'] for x in chunks))
        self.assertTrue(all(x['table_refs'][0]['full_row_reference_available'] for x in chunks))
        self.assertTrue(all(x['body_tokens'] <= 48 for x in chunks))

    def test_long_heading_kept_in_metadata_and_body(self):
        heading = 'Reservation ' * 120
        doc = document([{'type': 'heading', 'heading_path': [heading], 'text': heading}])
        chunks = self.chunker.structured(doc)
        self.chunker.validate(doc, chunks)
        self.assertTrue(all(x['heading_path'] == [heading] for x in chunks))
        self.assertTrue(all('heading_prefix_shortened' in x['flags'] for x in chunks))

    def test_ids_stable_and_change_with_config_or_content(self):
        doc = document([paragraph('Reservations are required. ' * 25)])
        original = self.chunker.fixed(doc)
        self.assertEqual(original, self.chunker.fixed(copy.deepcopy(doc)))
        changed = copy.deepcopy(self.config)
        changed['fixed_overlap_tokens'] = 7
        self.assertNotEqual(original[0]['chunk_id'], c.Chunker(changed).fixed(doc)[0]['chunk_id'])
        modified = document([paragraph('Reservations are required in summer. ' * 25)])
        self.assertNotEqual(original[0]['chunk_id'], self.chunker.fixed(modified)[0]['chunk_id'])
        self.assertNotEqual(original[0]['chunk_id'], self.chunker.structured(doc)[0]['chunk_id'])

    def test_rechunk_offline_removes_old_snapshot_and_ids(self):
        doc = document([paragraph('Campsite information is available. ' * 40)])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'documents.jsonl'
            source.write_text(json.dumps(doc) + '\n')
            output = base / 'chunks'
            with patch('socket.socket', side_effect=AssertionError('Network access forbidden')):
                first = c.build_snapshot(source, self.config, output)
                old_ids = c.validate_snapshot(output / 'versions' / first['version'])
                alternate = copy.deepcopy(self.config)
                alternate['target_body_tokens'] = 64
                second = c.build_snapshot(source, alternate, output)
            active = json.loads((output / 'active.json').read_text())['version']
            self.assertEqual(active, second['version'])
            self.assertFalse((output / 'versions' / first['version']).exists())
            self.assertEqual(len(list((output / 'versions').iterdir())), 1)
            self.assertFalse(old_ids & c.validate_snapshot(output / 'versions' / active))
            self.assertGreater(second['migration']['old_only_ids_removed'], 0)

    def test_failed_switch_retains_active_and_cleans_replacement(self):
        doc = document([paragraph('A campsite reservation is required. ' * 10)])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'documents.jsonl'
            source.write_text(json.dumps(doc) + '\n')
            output = base / 'chunks'
            original = c.build_snapshot(source, self.config, output)
            alternate = copy.deepcopy(self.config)
            alternate['target_body_tokens'] = 64
            real_replace = c.os.replace
            def fail_pointer(src, dst):
                if Path(dst).name == 'active.json':
                    raise OSError('Simulated pointer-switch failure')
                return real_replace(src, dst)
            with patch.object(c.os, 'replace', side_effect=fail_pointer):
                with self.assertRaises(OSError):
                    c.build_snapshot(source, alternate, output)
            self.assertEqual(json.loads((output / 'active.json').read_text())['version'], original['version'])
            self.assertEqual([p.name for p in (output / 'versions').iterdir()], [original['version']])
            c.validate_snapshot(output / 'versions' / original['version'])

    def test_tokenizer_integrity_and_invalid_overlap(self):
        invalid = copy.deepcopy(self.config)
        invalid['tokenizer']['sha256'] = 'wrong'
        with self.assertRaises(ValueError):
            c.Chunker(invalid)
        invalid = copy.deepcopy(self.config)
        invalid['fixed_overlap_tokens'] = invalid['target_body_tokens']
        with self.assertRaises(ValueError):
            c.Chunker(invalid)

    def test_corrupt_snapshot_cannot_replace_active(self):
        doc = document([paragraph('A campsite reservation is required. ' * 10)])
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'documents.jsonl'
            source.write_text(json.dumps(doc) + '\n')
            output = base / 'chunks'
            result = c.build_snapshot(source, self.config, output)
            path = output / 'versions' / result['version'] / 'fixed_overlap.jsonl'
            path.write_text(path.read_text().replace('required', 'garbled'))
            with self.assertRaises(ValueError):
                c.build_snapshot(source, self.config, output)
            self.assertEqual(json.loads((output / 'active.json').read_text())['version'], result['version'])


if __name__ == '__main__':
    unittest.main()
