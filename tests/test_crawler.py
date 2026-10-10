import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('crawler', Path(__file__).resolve().parents[1] / 'scripts/crawl_sources.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
CONFIG = {'allowed_host': 'www.nps.gov', 'allowed_path_prefixes': ['/yose/planyourvisit/'],
          'requests_per_second': 1, 'minimum_html_pages': 1, 'minimum_pdfs': 1, 'minimum_html_table_pages': 1}
URL = 'https://www.nps.gov/yose/planyourvisit/index.htm'


class CrawlerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 0
        self.calls = []

    def sleep(self, seconds):
        self.now += seconds

    def crawler(self, responses):
        def transport(url):
            self.calls.append((url, self.now))
            return responses.pop(0)
        return c.Crawler(CONFIG, self.tmp.name, 'project@example.org', transport, lambda: self.now, self.sleep)

    def test_email_required(self):
        with self.assertRaises(ValueError):
            c.Crawler(CONFIG, self.tmp.name, None)

    def test_raw_metadata_and_rate_limit(self):
        crawler = self.crawler([(200, {}, b'User-agent: *\nDisallow: /ns/'),
                                (200, {'Content-Type': 'text/html'}, b'<p>Hello</p>')])
        crawler.load_robots()
        r = crawler.crawl_one(URL)
        self.assertGreaterEqual(self.calls[1][1] - self.calls[0][1], 1)
        self.assertEqual((Path(self.tmp.name) / r['raw_path']).read_bytes(), b'<p>Hello</p>')
        self.assertIn('crawl_timestamp', r)
        self.assertEqual(len(Path(crawler.manifest).read_text().splitlines()), 2)

    def test_external_redirect_never_requested(self):
        crawler = self.crawler([(200, {}, b'User-agent: *'), (302, {'Location': 'https://evil.example/a.pdf'}, b'')])
        crawler.load_robots()
        with self.assertRaises(ValueError):
            crawler.crawl_one(URL)
        self.assertEqual(len(self.calls), 2)

    def test_robots_denial_prevents_request(self):
        crawler = self.crawler([(200, {}, b'User-agent: *\nDisallow: /yose/')])
        crawler.load_robots()
        with self.assertRaises(ValueError):
            crawler.crawl_one(URL)
        self.assertEqual(len(self.calls), 1)

    def test_wildcard_robots(self):
        robots = c.Robots(b'User-agent: *\nDisallow: /*loader.cfm*', 'ParkGuideAI')
        self.assertFalse(robots.allows('https://www.nps.gov/yose/loader.cfm?x=1'))
        self.assertTrue(robots.allows(URL))

    def test_robots_unavailable_fails_closed(self):
        crawler = self.crawler([(503, {}, b'Unavailable')])
        with self.assertRaises(RuntimeError):
            crawler.load_robots()
        self.assertIsNone(crawler.robots)

    def test_server_throttle_stops_run(self):
        crawler = self.crawler([(200, {}, b'User-agent: *'), (429, {}, b'Slow down')])
        crawler.load_robots()
        with self.assertRaises(RuntimeError):
            crawler.crawl_one(URL)

    def test_scope(self):
        for url in ['http://www.nps.gov/yose/planyourvisit/a.htm',
                    'https://www.nps.gov.evil.example/yose/planyourvisit/a.htm',
                    'https://www.nps.gov/yose/planyourvisit/%2e%2e/staff/a.htm',
                    'https://www.nps.gov/yose/planyourvisit/ymcstaff.htm',
                    'https://www.nps.gov/yose/planyourvisit/a.htm?login=1']:
            self.assertFalse(c.in_scope(url, CONFIG))
        self.assertTrue(c.in_scope(URL, CONFIG))

    def test_layout_tables_excluded(self):
        parser = c.TableCounter()
        parser.feed('<table id="cs_idLayout2"><tr><td>Layout<table><tr><th>Fee</th></tr></table></td></tr></table>')
        self.assertEqual(parser.tables, 1)

    def test_resume_requires_intact_successful_response(self):
        crawler = self.crawler([(200, {}, b'User-agent: *'),
                                (200, {'Content-Type': 'text/html'}, b'valid'),
                                (404, {}, b'missing')])
        crawler.load_robots()
        record = crawler.crawl_one(URL)
        crawler.crawl_one(URL.replace('index', 'missing'))
        self.assertEqual(c.completed_urls(self.tmp.name), {URL})
        (Path(self.tmp.name) / record['raw_path']).write_bytes(b'changed')
        self.assertEqual(c.completed_urls(self.tmp.name), set())

    def test_audit_deduplicates_and_checks_integrity(self):
        html = b'<table><tr><th>Fee</th></tr><tr><td>10</td></tr></table>'
        crawler = self.crawler([(200, {}, b'User-agent: *'),
                                (200, {'Content-Type': 'text/html'}, html),
                                (200, {'Content-Type': 'text/html'}, html),
                                (200, {'Content-Type': 'application/pdf'}, b'%PDF-test')])
        crawler.load_robots()
        crawler.crawl_one(URL)
        crawler.crawl_one(URL.replace('index', 'copy'))
        pdf = crawler.crawl_one(URL.replace('index.htm', 'guide.pdf'))
        summary = c.audit(self.tmp.name, CONFIG)
        self.assertEqual(summary['distinct_raw_documents'], {'html': 1, 'pdf': 1, 'html_with_tables': 1})
        self.assertTrue(summary['collection_minimums_met'])
        (Path(self.tmp.name) / pdf['raw_path']).write_bytes(b'corrupted')
        with self.assertRaises(ValueError):
            c.audit(self.tmp.name, CONFIG)


if __name__ == '__main__':
    unittest.main()
