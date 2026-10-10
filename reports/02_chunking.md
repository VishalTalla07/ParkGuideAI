# ParkGuide AI — Chunking Comparison and Re-chunking

Date: October 9, 2026

## Scope

Both strategies use the same 420-document normalized corpus (414 HTML pages and six PDFs). This report compares chunk structure and token sizes. Retrieval quality has not been evaluated and no strategy is claimed to be more accurate.

## Configuration

Tokenizer model: sentence-transformers/all-MiniLM-L6-v2. Pinned revision: `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The local tokenizer file is checksum-verified. Padding and truncation are disabled. Model length configuration specifies 256 tokens. Both chunkers target a body of at most 200 tokenizer tokens and prepend park/heading context capped at 40 tokens. Full embedding-input counts include the prefix and special tokens; observed maximum is 242.

Fixed chunks use 40-token overlap across a flattened document. Source character offsets map each chunk back to normalized blocks. Crossing a heading, cutting a table row, or splitting a wordpiece is recorded as a flag. Each substring is retokenized to verify that boundary changes do not break its budget.

Heading-aware chunks pack sentence units within one heading/page scope. Sentence splitting is heuristic rather than a linguistic parser. Normal HTML table rows stay intact and header rows repeat across table chunks. If headers exceed half the body budget, they receive separately referenced chunks. Oversized rows split at cell boundaries with cell-number labels; oversized cells split into bounded fragments. The complete source table/row remains accessible through references. These cases are flagged and covered by tests. No oversized rows occurred with the default configuration on this corpus.

PDF source text is chunked within page scopes with page numbers preserved. Parsed PDF tables remain in normalized storage; the current PDF extraction does not establish reliable text-to-row alignment. PDF chunks retain layout-review flags. This is a limitation of the present table-aware comparison, which operates on structured HTML table blocks.

Full heading paths remain in metadata and original heading body blocks even when the embedding prefix is shortened. Tokenizable source characters are checked for coverage in each strategy. Whitespace, table serialization separators, and characters intentionally discarded by BERT normalization are excluded from that coverage check.

## Observed sizes

| Strategy | Chunks | Body min | Body median | Body p95 | Body max | Full input min | Full input median | Full input p95 | Full input max |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Fixed size with overlap | 3270 | 41 | 200 | 200 | 200 | 50 | 213 | 232 | 242 |
| Heading aware | 5148 | 1 | 89 | 197 | 200 | 6 | 108 | 212 | 242 |

p95 uses the nearest-rank empirical percentile. Full-input counts are the values that the future embedding call must respect.

## Full-input token distributions

| Token range | Fixed overlap | Heading aware |
|---|---:|---:|
| 1-50 | 1 | 1198 |
| 51-100 | 110 | 1227 |
| 101-150 | 144 | 856 |
| 151-200 | 124 | 1008 |
| 201-256 | 2891 | 859 |

## Boundary and review flags

| Flag | Fixed overlap chunks | Heading-aware chunks |
|---|---:|---:|
| crosses_heading_boundary | 1939 | 0 |
| heading_prefix_shortened | 41 | 56 |
| oversized_sentence_split | 0 | 56 |
| pdf_layout_requires_review | 432 | 403 |
| table_header_inferred | 0 | 67 |
| table_row_cut | 226 | 0 |
| wordpiece_boundary_split | 483 | 10 |

Flag counts are numbers of chunks, not distinct sources or rows. Some headings are very short, so heading-aware chunks can have small bodies; their source context is retained in the prefix and metadata. Granularity, repeated headers, overlap, and extra candidates may affect retrieval differently. These structural measurements do not prove an accuracy improvement.

## IDs and provenance

Chunk IDs are SHA-256 hashes of a canonical object containing document ID, normalized content hash, chunker name/version, full parameters including tokenizer revision/checksum, chunk position, and full-input hash. Repeating a build with identical inputs produces identical IDs. Content/configuration changes produce new identities.

Every chunk preserves source URL, document/raw hashes, park, title, page type, crawl timestamp, heading paths, block character ranges, PDF page numbers when applicable, table references, source dates, coverage periods, review status, and token counts. Manifests list exact chunk IDs and SHA-256 hashes for each output file. The corpus fingerprint also covers normalized structure and metadata so a changed heading or review decision cannot silently reuse an obsolete snapshot.

## Offline migration demonstration

The complete corpus was built with 200-token bodies, rebuilt with 160-token bodies, then restored to 200. Socket access was blocked during all three builds, and only saved normalized documents/local tokenizer files were read.

- Initial/default version: `11fb50853ffee8a5fb4c555e3d70ddee4128ac765fc0fb6e61e621b9b8c496da`
- Alternate version: `417d974609800043fb0298d535620a1e376d15c0183e24bd0900298d8077d236`
- Restored version: `11fb50853ffee8a5fb4c555e3d70ddee4128ac765fc0fb6e61e621b9b8c496da`
- Removed old-only chunk IDs after restoration: 10,048
- Active manifest verified; original default version reproduced; zero orphan versions.

Builds take an exclusive file lock. A staged replacement is validated before an atomic active-pointer switch; failed switches retain the prior version and remove the replacement. Superseded local chunk versions are deleted only after activation. Configuration and statistical receipts are preserved under data/audit/chunk_runs/.

This demonstrates local chunk snapshot migration. Chroma collection deletion, sparse index replacement, and retrieval-score comparisons remain to be implemented with the retrieval stage.

## Tests and reproducibility

All 28 tests passed, including ten chunking/migration tests. Tests cover token limits, source coverage, overlap, heading boundaries, repeated headers, oversized rows, long headings, stable/change-sensitive IDs, tokenizer integrity, network-blocked migration, failed activation cleanup, and corrupted snapshot rejection. The standalone demo command was also exercised on a temporary corpus.

Run after installation and extraction:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/chunk_sources.py
.venv/bin/python scripts/demo_rechunk.py
```

Configuration: config/chunking.json. Active data: data/chunks/active.json and its referenced version directory. Machine-readable results: data/audit/chunking_summary.json and data/audit/rechunk_demo.json. Generated chunk bodies and ID manifests stay outside Git.

## Review and evaluation still required

All 420 documents have privacy/temporal review pending. Consequently, both strategies currently have zero chunks marked ready_for_index. No vectors or search indexes have been built. Review the corpus before indexing; author and human-verify the 40 evaluation questions before tuning. Compare retrieval on the same frozen corpus/evidence set and document failures before selecting a final strategy.

AI assistance: Codex implemented and tested the chunkers, pinned the tokenizer, ran structural comparisons and offline migration, and drafted this report. No gold answers or performance scores were generated.
