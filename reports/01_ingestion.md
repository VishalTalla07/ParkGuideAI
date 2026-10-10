# ParkGuide AI — Collection and Extraction Report

Date: October 9, 2026

## Results

| Measure | Count |
|---|---:|
| Distinct retained raw HTML responses | 456 |
| Distinct retained raw PDFs | 6 |
| Extracted HTML documents | 414 |
| Extracted PDF documents | 6 |
| HTML documents with data-table candidates | 49 |

The automated collection and retention counts exceed the planned minimums of 150 HTML pages, five PDFs, and three table pages. These are extraction results, not a final human-approved corpus. No retrieval or chatbot performance was measured.

## Scope and crawl policy

Source: https://www.nps.gov. Approved paths cover visitor-planning content for Yosemite, Yellowstone, and Grand Canyon. Crawling used a descriptive ParkGuideAI User-Agent with the supplied contact email, fresh robots rules, HTTPS verification, sequential requests at least one second apart, and scope/robots checks before following redirects. Raw response bodies were saved by SHA-256 before parsing; metadata records URL, UTC crawl timestamp, status, headers, and raw hash. Server throttling and server errors stop collection.

One clinic staff directory was discovered during the final review. Its raw body was deleted, its manifest entry marked excluded, and the filename filter tightened to prevent recollection. Its metadata is retained solely as an exclusion audit. Initial pathname-segment filtering alone was insufficient.

## Extraction and provenance

HTML extraction uses BeautifulSoup/lxml and the main-content landmark. Known navigation, modals, breadcrumbs, scripts, footers, and CommonSpot layout scaffolding are removed. Heading paths and semantic text blocks are preserved. HTML table rows retain cell strings, header markers, rowspan, and colspan values rather than guessing flattened alignment.

PDF extraction uses pdfplumber. A pypdf in-memory parsing copy removes page rotation; panels with opposite glyph orientations are read separately in content-stream order. Original raw bytes stay unchanged. PDF page numbers, extracted tables, titles, and discoverable cover date ranges are retained. Multicolumn reading order and detected PDF tables still require review.

Documents have deterministic SHA-256 IDs based on source URL, normalized-content hashes, raw hashes, URL, park, page type, title, timestamp, and block-level heading paths. Exact normalized-content duplicates are excluded if present. Output JSONL snapshots are rebuilt from raw files without crawling.

## Retained documents by park

| Park | HTML and PDF documents |
|---|---:|
| Yosemite | 201 |
| Yellowstone | 95 |
| Grand Canyon | 124 |

## Exclusions

| Reason | Count |
|---|---:|
| dated_event_outside_visitor_planning_scope | 10 |
| http_301 | 7 |
| insufficient_text_manual_review | 23 |
| translated_page_outside_initial_english_corpus | 9 |
| personal_directory | 1 |

HTTP 301 entries are redirect-source records; destination responses are considered independently. Short or missing text is flagged for manual review instead of filling the quota with empty pages. Known translated pages are excluded from this first English corpus. Dated event exclusions are heuristic; all retained pages still need a relevance check.

## Table spot-checks

The following extracted examples contain actual multirow data tables rather than CommonSpot layout tables. These checks do not replace team review of every indexed table.

| Source | Extracted structure checked |
|---|---|
| [Yosemite campgrounds](https://www.nps.gov/yose/planyourvisit/campgrounds.htm) | Campground, opening information, elevation, reservation, fee, site counts, accessibility, and water columns; section-spanning rows preserved. |
| [Yellowstone operating dates](https://www.nps.gov/yell/planyourvisit/operating-dates.htm) | Facility/service, status, summer and winter opening/closing dates; original multilevel header rows retained. |
| [Grand Canyon trail distances](https://www.nps.gov/grca/planyourvisit/trail-distances.htm) | Rim distance, elevation, amenities, and trail notes retained as separate cells. |

## Privacy and temporal controls

Recognizable emails, phone numbers (including slash-separated NPS phone formats), and SSN patterns are redacted before normalized output. Institutional contacts are conservatively redacted too. Counts measure repeated text occurrences across blocks and tables, not distinct people. Automated patterns cannot certify complete PII/PHI removal. All documents carry privacy and temporal review status pending.

Page last-updated dates are distinct from policy effective dates; effective dates remain unknown unless explicitly verified. Discoverable PDF cover date ranges are coverage periods. Historical editions remain distinguishable and must not be used to claim live operating conditions or availability.

## Verification and reproducibility

Eighteen automated tests passed. They cover robots denial and failure, rate limiting, redirect scope, raw hash integrity, resume behavior, layout-table rejection, text/heading extraction, table spans, redaction, rotated PDF reading order, date handling, exclusions, and repeatable offline extraction. Corpus-wide checks confirmed unique document IDs/content hashes, absent directory content, populated text blocks, and no remaining recognizable email pattern in normalized text.

Dependencies are pinned in requirements.txt; tested interpreter: Python 3.14.2. Setup and commands are in README.md. Machine-readable counts are in data/audit/collection_summary.json and data/audit/extraction_summary.json. Raw and normalized content are excluded from Git.

## Remaining work

Review data/normalized/review_inventory.csv against source text for relevance, privacy, table fidelity, and dates. Keep review decisions separately so rerunning extraction cannot overwrite them. Flag scanned or garbled PDF regions for correction or exclusion. Author and human-verify the 40 evaluation questions before retrieval tuning. Then compare fixed-overlap and heading-aware chunking on the same reviewed corpus.

AI assistance: Codex implemented and tested the ingestion code, ran the crawl, inspected extraction samples, and drafted this report. Human review is still pending.
