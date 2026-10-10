"""Build a candidate inventory from locally saved NPS sitemap sections.

This script performs no network requests and does not validate page content.
Run from the project root: python3 scripts/audit_sources.py
"""
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "config/sources.json").read_text())
audit = ROOT / "data/audit"
robots = RobotFileParser()
robots.parse((audit / "robots.txt").read_text().splitlines())
files = sorted(audit.glob("sitemap[1-5].xml"))
if len(files) != 5:
    raise SystemExit("Expected all five saved sitemap sections in data/audit.")

urls = set()
ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
for file in files:
    for element in ET.parse(file).findall(".//s:url/s:loc", ns):
        parsed = urlsplit(element.text or "")
        if parsed.scheme != "https" or parsed.hostname != config["allowed_host"]:
            continue
        if not any(parsed.path.startswith(prefix) for prefix in config["allowed_path_prefixes"]):
            continue
        if not parsed.path.endswith(".htm"):
            continue
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        if robots.can_fetch("NationalParksVisitorAssistant", url):
            urls.add(url)

counts = Counter(urlsplit(url).path.split("/")[1] for url in urls)
summary = {
    "audit_date": "2026-10-09",
    "status": "discovery_only_not_crawled",
    "candidate_html_pages": len(urls),
    "by_park": {config["parks"][park]: counts[park] for park in config["parks"]},
    "identified_html_table_pages": len(config["table_pages"]),
    "identified_pdf_candidates": len(config["pdf_candidates"]),
    "limitations": [
        "Sitemap candidates have not been individually fetched or checked for relevance.",
        "Final counts require successful downloads, content deduplication, and privacy review.",
        "Older seasonal PDF guides require effective-date metadata and historical filtering.",
        "Crawler contact email has not been supplied."
    ]
}
(audit / "candidate_urls.txt").write_text("\n".join(sorted(urls)) + "\n")
(audit / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary, indent=2))
