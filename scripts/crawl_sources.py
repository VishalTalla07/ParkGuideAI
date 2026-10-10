"""Collect an auditable NPS corpus using only Python's standard library."""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[1]


def canonical(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, '', ''))


def in_scope(url, config):
    p = urlsplit(url)
    path = unquote(p.path).lower()
    return (p.scheme == 'https' and p.netloc == config['allowed_host']
            and not p.query and not p.fragment
            and not any(x in path.split('/') for x in ('..', 'staff', 'directory', 'login'))
            and not any(word in path.rsplit('/', 1)[-1] for word in ('staff', 'directory', 'login'))
            and any(path.startswith(prefix) for prefix in config['allowed_path_prefixes'])
            and path.endswith(('.htm', '.html', '.pdf')))


class Robots:
    def __init__(self, body, agent):
        self.agent = agent
        self.parser = RobotFileParser()
        lines = body.decode('utf-8', errors='replace').splitlines()
        self.parser.parse(lines)
        # urllib.robotparser treats '*' literally. Conservatively enforce wildcard
        # disallows in the site's generic group as well as its ordinary rules.
        self.wildcards = []
        agents = []
        rules_started = False
        for line in lines:
            key, sep, value = line.split('#', 1)[0].partition(':')
            if not sep:
                continue
            key, value = key.strip().lower(), value.strip()
            if key == 'user-agent':
                if rules_started:
                    agents, rules_started = [], False
                agents.append(value.lower())
            elif key in ('allow', 'disallow'):
                rules_started = True
                if key == 'disallow' and '*' in agents and '*' in value:
                    end = value.endswith('$')
                    value = value[:-1] if end else value
                    self.wildcards.append(re.compile('^' + re.escape(value).replace(r'\*', '.*') + ('$' if end else '')))

    def allows(self, url):
        p = urlsplit(url)
        path = p.path + ('?' + p.query if p.query else '')
        return self.parser.can_fetch(self.agent, url) and not any(r.search(path) for r in self.wildcards)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TableCounter(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = 0
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            attributes = dict(attrs)
            layout = ('layout' in attributes.get('class', '').lower()
                      or attributes.get('id', '').lower().startswith('cs_idlayout')
                      or attributes.get('role', '') == 'presentation')
            self.stack.append({'layout': layout, 'cells': False})
        elif tag in ('td', 'th') and self.stack:
            self.stack[-1]['cells'] = True

    def handle_endtag(self, tag):
        if tag == 'table' and self.stack:
            table = self.stack.pop()
            if not table['layout'] and table['cells']:
                self.tables += 1


class Crawler:
    def __init__(self, config, directory, email, transport=None, clock=time.monotonic, sleep=time.sleep):
        if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email or ''):
            raise ValueError('Set PARKGUIDE_CONTACT_EMAIL to a real project contact email before crawling.')
        self.config = config
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.agent = f'ParkGuideAI/0.1 (course research; contact: {email})'
        self.opener = build_opener(NoRedirect())
        self.transport = transport or self.request
        self.clock, self.sleep = clock, sleep
        self.interval = max(1.0, 1.0 / config['requests_per_second'])
        self.last_start = None
        self.robots = None
        self.manifest = self.directory / 'manifest.jsonl'

    def request(self, url):
        req = Request(url, headers={'User-Agent': self.agent, 'Accept': 'text/html,application/pdf,text/plain'})
        try:
            response = self.opener.open(req, timeout=45)
        except HTTPError as exc:
            response = exc
        with response:
            # Do not parse anything until the complete response has been saved.
            body = response.read(25 * 1024 * 1024 + 1)
            if len(body) > 25 * 1024 * 1024:
                raise ValueError('Response exceeds the 25 MiB collection limit')
            return response.code, dict(response.headers), body

    def fetch(self, url, purpose='document'):
        if self.last_start is not None:
            self.sleep(max(0, self.interval - (self.clock() - self.last_start)))
        self.last_start = self.clock()
        status, headers, body = self.transport(url)
        digest = hashlib.sha256(body).hexdigest()
        blob = self.directory / 'blobs' / digest
        blob.parent.mkdir(exist_ok=True)
        if not blob.exists():
            blob.write_bytes(body)
        record = {'url': url, 'crawl_timestamp': datetime.now(timezone.utc).isoformat(),
                  'sha256': digest, 'status': status, 'headers': headers,
                  'raw_path': str(blob.relative_to(self.directory)), 'purpose': purpose}
        with self.manifest.open('a') as out:
            out.write(json.dumps(record) + '\n')
        return record, body

    def load_robots(self):
        url = f"https://{self.config['allowed_host']}/robots.txt"
        record, body = self.fetch(url, 'robots')
        if record['status'] != 200:
            raise RuntimeError('robots.txt unavailable: collection stopped (fail closed).')
        self.robots = Robots(body, self.agent)

    def crawl_one(self, url):
        url = canonical(url)
        for _ in range(6):
            if not in_scope(url, self.config):
                raise ValueError(f'Outside approved scope: {url}')
            if self.robots is None or not self.robots.allows(url):
                raise ValueError(f'Robots denied: {url}')
            record, body = self.fetch(url)
            headers = {k.lower(): v for k, v in record['headers'].items()}
            if record['status'] in (301, 302, 303, 307, 308):
                target = urljoin(url, headers.get('location', ''))
                if not in_scope(target, self.config):
                    raise ValueError(f'Redirect outside approved scope: {target}')
                url = target
                continue
            if record['status'] == 429 or record['status'] >= 500:
                raise RuntimeError(f"Server returned {record['status']}; stopped to avoid repeated requests.")
            return record
        raise ValueError('Redirect limit exceeded')


def audit(directory, config):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    latest = {}
    manifest = directory / 'manifest.jsonl'
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            r = json.loads(line)
            if r['purpose'] == 'document':
                latest[r['url']] = r
    seen, counts = set(), {'html': 0, 'pdf': 0, 'html_with_tables': 0}
    for r in latest.values():
        if r.get('excluded_reason') or r['status'] != 200 or r['sha256'] in seen:
            continue
        body = (directory / r['raw_path']).read_bytes()
        if hashlib.sha256(body).hexdigest() != r['sha256']:
            raise ValueError(f"Raw integrity failure: {r['url']}")
        mime = next((v for k, v in r['headers'].items() if k.lower() == 'content-type'), '').lower()
        if 'application/pdf' in mime and body.startswith(b'%PDF-'):
            counts['pdf'] += 1
        elif 'text/html' in mime:
            counts['html'] += 1
            parser = TableCounter()
            parser.feed(body.decode('utf-8', errors='replace'))
            counts['html_with_tables'] += int(parser.tables > 0)
        else:
            continue
        seen.add(r['sha256'])
    summary = {'distinct_raw_documents': counts,
               'collection_minimums_met': counts['html'] >= config['minimum_html_pages'] and counts['pdf'] >= config['minimum_pdfs'] and counts['html_with_tables'] >= config['minimum_html_table_pages'],
               'limitations': 'Counts are provisional: normalized-content deduplication, relevance, privacy review, and manual confirmation of data tables remain required.'}
    (directory / 'collection_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def completed_urls(directory):
    directory = Path(directory)
    manifest = directory / 'manifest.jsonl'
    completed = set()
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            r = json.loads(line)
            blob = directory / r['raw_path']
            if (not r.get('excluded_reason') and r['purpose'] == 'document' and r['status'] == 200 and blob.exists()
                    and hashlib.sha256(blob.read_bytes()).hexdigest() == r['sha256']):
                completed.add(r['url'])
    return completed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit', type=int, default=0, help='Maximum new URLs; 0 means all candidates')
    p.add_argument('--plan', action='store_true', help='Inspect the queue without network requests')
    p.add_argument('--audit-only', action='store_true')
    args = p.parse_args()
    config = json.loads((ROOT / 'config/sources.json').read_text())
    directory = ROOT / 'data/raw'
    if args.audit_only:
        print(json.dumps(audit(directory, config), indent=2))
        return
    candidates = (ROOT / 'data/audit/candidate_urls.txt').read_text().splitlines()
    urls = list(dict.fromkeys(config['table_pages'] + config['pdf_candidates'] + candidates))
    urls = [canonical(u) for u in urls if in_scope(u, config)]
    if args.limit < 0:
        p.error('--limit must be nonnegative')
    if args.plan:
        print(json.dumps({'eligible_urls': len(urls), 'pdf_candidates': sum(u.endswith('.pdf') for u in urls), 'network_requests': 0}, indent=2))
        return
    crawler = Crawler(config, directory, os.environ.get('PARKGUIDE_CONTACT_EMAIL') or config.get('contact_email'))
    completed = completed_urls(directory)
    crawler.load_robots()
    pending = [u for u in urls if u not in completed]
    if args.limit:
        pending = pending[:args.limit]
    try:
        for url in pending:
            try:
                record = crawler.crawl_one(url)
                print(f"{record['status']} {url}", flush=True)
            except ValueError as exc:
                print(f'SKIP {exc}', flush=True)
    finally:
        print(json.dumps(audit(directory, config), indent=2))


if __name__ == '__main__':
    main()
