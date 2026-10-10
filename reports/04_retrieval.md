# ParkGuide AI — Retrieval Implementation and Smoke Checks

Date: October 10, 2026

## Implemented retrieval modes

Dense cosine retrieval, BM25 keyword retrieval, and hybrid rank fusion are available for both chunking strategies. Each uses the same versioned chunk records and eligible source set. Dense and sparse retrievers each contribute up to 40 candidates; the selected mode retains up to 20 unique chunks. The CLI displays five passages while preserving the whole pool in an optional JSON trace. No generated answer or cross-encoder reranking is present yet.

BM25 uses rank-bm25 0.2.2 with k1=1.5 and b=0.75. Text is lowercased and split into ASCII alphanumeric terms, including heading context. Corpus IDF statistics stay fixed; eligibility filters mask the ranked candidates. The BM25 object is reconstructed from the versioned records when a retriever opens. No pickle or executable sparse serialization is used.

Hybrid retrieval sums 1/(60+rank) from each ranked list. Each chunk ID occurs once in the final pool. Cosine, BM25, and fusion scores and ranks are preserved separately; their scales are not interpreted as answer-confidence probabilities. Constants remain at the proposed defaults, with no tuning on the smoke queries.

Implementation references: [rank-bm25 source](https://github.com/dorianbrown/rank_bm25) and [Chroma query/filter API](https://docs.trychroma.com/reference/python/collection).

## Filters and evidence sufficiency

Park and page-type filters apply to both dense and sparse retrieval before candidate selection. A YYYY-MM-DD as-of filter requires an explicit known source coverage period. Unknown periods are excluded; page-update timestamps are not treated as policy effective dates. This conservative first temporal filter does not yet infer validity from HTML policy text.

A pool smaller than 20 is explicitly flagged, and filters are never silently relaxed. Keyword retrieval excludes zero-token-overlap ties; dense retrieval still returns nearest neighbors, which does not establish that an answer is supported. A later grounding/refusal gate must assess requested facts and conflicts rather than relying on retrieval scores alone.

## Engineering probes

Eight fixed smoke queries were run in both strategies and all three modes: 48 runs. Socket access was blocked during execution. Filter enforcement and unique candidate IDs passed. These probes have no human-verified gold answers/evidence labels and are not the required 40-item evaluation set. They must not be used to claim Recall, correctness, or an improvement from hybrid retrieval.

| Heading-aware hybrid probe | Candidates | Top retrieved source |
|---|---:|---|
| camping | 20 | [Help! I can't find a camping or lodging reservation.](https://www.nps.gov/yose/planyourvisit/reservehelp.htm) |
| fees | 20 | [Fees & Passes](https://www.nps.gov/yell/planyourvisit/fees.htm) |
| permits | 20 | [Backcountry Permit](https://www.nps.gov/grca/planyourvisit/backcountry-permit.htm) |
| accessibility_table | 20 | [Campgrounds](https://www.nps.gov/yose/planyourvisit/campgrounds.htm) |
| operating_dates_table | 20 | [Bridge Bay Campground](https://www.nps.gov/yell/planyourvisit/bridgebaycg.htm) |
| pdf_small_pool | 5 | [Day Hike - Rim Trail](https://www.nps.gov/grca/planyourvisit/upload/RimTrail.pdf) |
| dated_pdf | 20 | [Yosemite Guide - Volume 51, Issue 8](https://www.nps.gov/yose/planyourvisit/upload/Yosemite-Guide-51-8-508V1.pdf) |
| unknown_period | 0 | No eligible evidence |

There were 12 shortfall runs: the restricted Grand Canyon PDF pool had fewer than 20 chunks, and the requested unknown Yellowstone period had no eligible evidence. This is expected filter behavior, not a measured refusal result. No chatbot answers were generated.

Across this small local smoke sample, retrieval execution p50 was 9.58 ms and p95 was 19.07 ms. This excludes model loading, retriever initialization, reranking, generation, and the UI, and does not constitute a formal end-to-end benchmark. System: Darwin / arm64, four Torch CPU threads.

## Tests and lifecycle

All 41 project tests passed, including 13 retrieval/index tests. Tests cover vector shape/norm/finite checks, cosine ordering on synthetic fixtures, keyword ranking, identical eligibility filters, date exclusion, unique fusion IDs/scores, zero-overlap keyword queries, approved-corpus enforcement, manifest ID agreement, collection/sparse migration, failed activation rollback, interrupted-build recovery, and stale-retriever rejection. Synthetic test embeddings verify plumbing; real-model smoke probes verify local execution without supplying gold labels.

The full-corpus 200 → 160 → 200 migration restored the original index identity. Exact vector, sparse, and manifest IDs matched, with superseded collections/files deleted and no network access. Statistical receipts survive separately from generated indexes.

## Reproduce

```sh
.venv/bin/python scripts/search_sources.py "How do I reserve a campground in Yosemite?" --park yose --mode hybrid --development-preview
.venv/bin/python scripts/search_sources.py "Which Yosemite campgrounds have accessible sites?" --park yose --page-type html --mode bm25 --development-preview
.venv/bin/python scripts/smoke_retrieval.py
.venv/bin/python scripts/demo_index_migration.py
.venv/bin/python -m unittest discover -s tests -v
```

Use --strategy fixed_overlap for the baseline and --json-output PATH for complete evidence traces. Human review remains pending; --development-preview explicitly selects the separately labeled engineering index. Omit it only after reviewed source metadata has been applied and an approved index built.

## Next stages and evaluation

Add cross-encoder reranking to the 20-candidate pool, retaining at most five passages with pre/post scores and latency. Implement context-only generation, claim-level source citations, insufficient-context refusals, and chat history. Author and human-verify the 40-item golden set before tuning; freeze dev/test split and compare B0–B3 and one-factor ablations on the same evidence definitions. Formal retrieval/answer/citation/refusal metrics remain uncomputed.

AI assistance: Codex implemented retrieval, ran engineering probes, inspected source URLs, and drafted this report. No human labels or formal quality scores were invented.
