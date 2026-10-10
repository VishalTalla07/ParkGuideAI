# ParkGuide AI

A step-by-step MSAI 633 project that will answer visitor questions using saved public National Park Service content for Yosemite, Yellowstone, and Grand Canyon.

GitHub repository: [VishalTalla07/ParkGuideAI](https://github.com/VishalTalla07/ParkGuideAI).

## Current milestone

Collection, extraction, both chunkers, local embeddings, dense/BM25/hybrid retrieval, cross-encoder reranking, Gemini generation and the Streamlit chat interface are implemented. The local approved index contains 3,270 fixed-overlap and 5,148 heading-aware chunks from 420 documents after user-reported corpus review. Generation validates structured claims and exact supporting quotes, constructs NPS citations, and refuses unsupported answers. Quota exhaustion displays “Free quota reached—please try again later.” without automatic retries. All 86 tests pass.

The project owner approved 40 evaluation questions; the 24-dev/16-test split is frozen locally. Development retrieval evaluation is complete, while live development answer collection stopped after 16 completed requests when the free quota was reached. Answer correctness remains unscored and the test set is unused. See reports 01–06 for evidence and limitations.

This repository contains code, configuration, tokenizer assets, tests and reports. API keys, downloaded models, corpus snapshots, review records, evaluation traces and generated output files stay local and are excluded from Git. A fresh clone needs the ingestion, review, model download and index-building steps below; it does not contain the project's existing local runtime data. Never commit `.env` or `.streamlit/secrets.toml`.

See `reports/00_proposal.md` for the full proposal, proposed team roles, chosen stack, architecture diagram, chunk migration design, grounding, evaluation, risks, and ethics. Member names and verified GitHub setup are still required before submission. Source URLs and collection requirements are in `config/sources.json`.

## Reproduce the source inventory

The audit uses Python's standard library. Save the NPS sitemap index and its five sitemap sections under `data/audit/`, along with `https://www.nps.gov/robots.txt`. Make requests sequentially, at least one second apart. The files are named `sitemap.xml`, `sitemap1.xml` through `sitemap5.xml`, and `robots.txt`.

Then run from this folder:

```sh
python3 scripts/audit_sources.py
```

This produces `data/audit/candidate_urls.txt` and `data/audit/summary.json`. The sitemap inventory counts candidates, not verified corpus documents.

## Collect the raw corpus

The crawler uses Python's standard-library HTTP client and robots parser, so this stage needs no third-party packages. It checks fresh robots rules, enforces the approved park paths on every redirect, sends a contact User-Agent, and makes requests at least one second apart. Server throttling or server errors stop the run. Bodies are stored by SHA-256 in `data/raw/blobs/`; `manifest.jsonl` records URL, timestamp, status, headers, and hash before parsing. Raw content remains excluded from Git.

Preview the queue without making requests:

```sh
python3 scripts/crawl_sources.py --plan
python3 -m unittest discover -s tests -v
```

Provide a real contact email in your terminal environment, then run a small pilot:

```sh
export PARKGUIDE_CONTACT_EMAIL='your-project-contact-email'
python3 scripts/crawl_sources.py --limit 12
```

After reviewing the pilot, collect the remaining URLs:

```sh
python3 scripts/crawl_sources.py
python3 scripts/crawl_sources.py --audit-only
```

Repeat runs skip successfully saved responses with verified hashes. The queue contains 462 HTML candidates and six PDF candidates; these are discovery counts. `collection_summary.json` reports distinct raw HTML, PDFs, and HTML containing table markup. Relevance, duplicate normalized text, meaningful data tables, and privacy still require review. Targets are at least 150 HTML pages, five PDFs, and three HTML data-table pages.

## Next milestone

Finish the remaining development answers when quota is available, review generated-answer correctness, complete B0–B3 comparisons and failure analysis, then freeze configuration before the final test run. A contact email was supplied for the successful pilot; set it in the environment when running future crawls. On macOS Python installations missing their certificate bundle, use `SSL_CERT_FILE=/etc/ssl/cert.pem` with the commands above; HTTPS verification remains enabled.

## Extract saved content

Create an isolated environment and install the pinned extraction dependencies (tested with Python 3.14.2):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/extract_sources.py
```

Extraction uses BeautifulSoup with lxml for HTML, pdfplumber for PDF text/tables, and pypdf to normalize page rotation in an in-memory parsing copy. It makes no network requests and never changes saved raw responses. Known navigation and CommonSpot layout scaffolding are removed; heading paths, table cells with rowspan/colspan metadata, and PDF page numbers are preserved.

`data/normalized/documents.jsonl` contains deterministic URL-based document IDs, raw and normalized hashes, URLs, titles, parks, page types, timestamps, text, blocks, and tables. Exact normalized duplicates, insufficient text, dated events outside scope, and known translated pages are recorded in `exclusions.jsonl`. Each run rebuilds these outputs from raw files, replacing the previous snapshot.

Recognizable email addresses, telephone numbers, and SSN patterns are redacted conservatively, including institutional contacts. Automated redaction does not certify that all PII/PHI has been removed. Review `review_inventory.csv` and the corresponding source text before approving content for chatbot use; keep decisions in a separate file so rerunning extraction cannot overwrite them. Check relevance, remaining personal information, scanned/multicolumn PDF quality, and table cell alignment. Every extracted document starts with privacy and temporal review marked pending.

Page-update dates are stored separately from effective dates. Policy effective dates remain unknown unless explicitly verified. Stated PDF cover date ranges are recorded as coverage periods. The chatbot must label cached information and avoid representing historical guides or saved closure/availability information as live.

## Compare chunks and demonstrate re-chunking

The tokenizer and model length configuration are saved locally in `assets/tokenizer/` at a pinned model revision. Chunking requires no model weights or network access. Install the updated pinned dependencies, then run:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/chunk_sources.py
.venv/bin/python scripts/demo_rechunk.py
```

`config/chunking.json` sets a 200-token body target, 40-token fixed overlap, a 40-token heading prefix budget, and a 256-token complete-input limit. Fixed chunks may cross heading or table-row boundaries; those cuts are flagged. Heading-aware chunks pack sentence units under the same heading/page, keep normal HTML table rows intact, and repeat headers when they fit. Oversized rows/cells retain full-table references and explicit split flags. Full headings remain in metadata and heading body blocks when an embedding prefix is shortened. PDF text is grouped by page; its extracted table structures remain in normalized storage for review rather than being treated as verified row-aligned text.

Each chunk has a deterministic ID, source URL/document hash, source block character ranges, heading paths, table references, page numbers, date metadata, review status, token counts, and its complete future embedding input. A coverage check verifies that both strategies retain all tokenizable source characters, excluding whitespace and table serialization pipes.

Snapshots live in `data/chunks/versions/<version>/`, with separate JSONL files for the two strategies and a manifest containing exact expected IDs and file hashes. `data/chunks/active.json` identifies the active snapshot. Builds take an exclusive lock, validate before switching the pointer atomically, then remove superseded local chunk versions. A failed switch preserves the prior snapshot and removes the replacement. Statistical summaries survive under `data/audit/chunk_runs/`; generated chunks are excluded from Git.

The demo blocks socket access, builds the default configuration, changes the target to 160 tokens, restores 200, and verifies reproducible IDs and absence of stale versions. It demonstrates local chunk-file migration only. Chroma and BM25 migration will be implemented with the retrieval stage.

New extraction snapshots begin with pending privacy/temporal review and `ready_for_index=false`. Local development preview artifacts preserve those flags. The approved index builder requires both review statuses to be approved and `ready_for_index=true`. The existing local snapshot was approved separately; new snapshots require review before generation. Gold questions also require human verification before tuning or scoring.

## Build and inspect retrieval

Install the updated pinned dependencies and download the pinned public embedding files once:

```sh
.venv/bin/python -m pip install -r requirements.txt
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python scripts/download_embedding_model.py
.venv/bin/python scripts/build_index.py --development-preview
```

The model download uses a fixed repository revision and saves file checksums. Embedding and retrieval load local files with remote model code disabled. Documents remain local. Sentence Transformers produces L2-normalized 384-dimensional vectors on CPU; Chroma stores them with cosine distance. The embedding tokenizer must match the chunking tokenizer, and overlong embedding inputs/queries are rejected rather than silently truncated. Vectors are cached by model identity and full-input hash.

Search all three modes against either chunking strategy:

```sh
.venv/bin/python scripts/search_sources.py "How do I reserve a campground in Yosemite?" --park yose --mode hybrid --development-preview
.venv/bin/python scripts/search_sources.py "Which Yosemite campgrounds have accessible sites?" --park yose --page-type html --mode bm25 --development-preview
.venv/bin/python scripts/search_sources.py "What distances are listed on the Rim Trail?" --park grca --page-type pdf --mode dense --development-preview
```

`--strategy fixed_overlap` selects the baseline; `heading_aware` is the default. Each retriever supplies up to 40 candidates and the selected mode retains up to 20 unique chunks. Hybrid retrieval uses reciprocal rank fusion with constant 60. Park and page-type filters apply equally to dense and sparse candidates. `--as-of YYYY-MM-DD` requires a known coverage period and excludes unknown periods. A pool with fewer than 20 is reported explicitly without relaxing filters.

The terminal shows the first five retrieved passages and source URLs; it does not yet produce chatbot answers. Use `--json-output /path/to/trace.json` to save the complete pool, separate cosine/BM25/RRF scores, source metadata, flags, and timing. These retrieval scores are not calibrated answer-confidence probabilities.

Development and approved indexes live in separate subdirectories under `data/index/`. Approved-mode commands omit `--development-preview`. The approved index was built after the user-reported all-corpus review; both modes reject inconsistent manifests and preserve review metadata. Both modes retain review metadata. BM25 is rebuilt deterministically from the versioned chunk records at query initialization, avoiding pickle files. Persistent vector collections, sparse source snapshots, model configuration, and expected IDs share one manifest.

Run the engineering probes and migration demonstration:

```sh
.venv/bin/python scripts/smoke_retrieval.py
.venv/bin/python scripts/demo_index_migration.py
.venv/bin/python -m unittest discover -s tests -v
```

The migration blocks socket access, re-chunks the same normalized corpus at 160 tokens, replaces both vector collections and sparse snapshots, then restores 200 tokens. It validates exact IDs before switching the active pointer and deletes superseded collections/files. File locks protect builds and searches; a retriever loaded before a migration must be reloaded after the switch. Failed activation preserves the prior index, and interrupted collections are cleared on retry. Embedding cache files are reusable model/input artifacts rather than searchable old chunk records.

Eight smoke queries ran across both strategies and three modes, with filter/unique-ID checks passing. Small PDF and unknown-period pools were correctly reported as shortfalls. The probes are engineering checks, not the 40-item gold set or evidence of answer accuracy. Results and migration receipts are in `data/audit/retrieval_smoke.json` and `data/audit/index_migration_demo.json`.

The team must author and human-verify at least 40 evaluation items before tuning the chatbot. Do not use system output as the basis for gold answers.

## Launch the chat interface

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py
```

Open http://127.0.0.1:8501. The server binds to the local computer only. The interface selects the reviewed corpus by default. When its API key is available, it selects Cited answer automatically, including an existing session that was using preview before the key was added. An explicit later Source preview selection is preserved. Source preview needs no API key; the development index remains available by deselecting Use reviewed corpus. Historical preview replies remain unchanged; ask a new question to use the newly selected mode. It displays up to five short source excerpts and clickable NPS links, not a generated answer.

Select a park, HTML/PDF sources, and an optional known-coverage date. Unknown coverage is excluded by the date filter; a crawl/update date is not proof of current conditions. Each reply keeps its original filters and expandable pre/post ranking trace, separate stage timings, model usage when available, and a local trace download.

Conversation history lives in memory for the current browser session. Clear conversation resets it; reloading may create a new session. Select an earlier question under Follow-up to when referring to a previous topic. Only that selected question is added as context; assistant messages never serve as evidence. This is explicit single-question context selection, not automatic conversation summarization.

Cited answer mode remains gated on an approved corpus and a locally configured `GEMINI_API_KEY`. If enabled later, the interface discloses that the question, selected earlier question, and source passages are sent to Google Gemini. Keys are not entered into the interface. No live answer generation has been verified. Emergency/personal medical patterns receive a referral to emergency services or a park ranger; the pattern check is limited and not a reliable medical classifier.

Eleven new chat orchestration and Streamlit AppTest checks cover history, follow-ups, filters, separate sessions, clearing chat, explicit refusals, referrals, and sanitized errors. The full suite passed 67 tests. Browser testing confirmed real-corpus retrieval and source/trace display. Details are in `reports/05_answering.md`.

## Rerank evidence and prepare answers

Download the pinned cross-encoder once, then inspect up to five reranked passages:

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python scripts/download_reranker.py
.venv/bin/python scripts/ask_parkguide.py "How do I reserve a campground in Yosemite?" --park yose --development-preview --json-output tmp/question_trace.json
.venv/bin/python scripts/smoke_answering.py
```

The trace preserves all 20 candidates, separate retrieval and reranker scores, pre/post ranks, and timings. The preview prints source evidence rather than a generated answer. An empty pool remains empty. Cross-encoder inputs over 512 tokens fail explicitly.

The active generation adapter uses Google Gemini (`gemini-3.5-flash-lite`) with a claim/evidence JSON schema. It constructs citations from stored source URLs and refuses invalid evidence, long copied runs, incomplete outputs, and insufficient context. Exact quote matching proves provenance, not semantic entailment; human evaluation remains necessary. The earlier OpenAI adapter is retained for reproducibility but is not selected by the current configuration. See `reports/05_answering.md`.

### Add your Gemini key

The `.env` file is in the project root, beside `app.py`. Open it locally and fill in:

```dotenv
GEMINI_API_KEY=your_api_key_here
```

Replace the placeholder with your Google AI Studio key and save. The application reads this file on each request and sidebar refresh; an existing nonempty process environment variable takes precedence. `.env` is excluded from Git and is never included in traces. `.env.example` contains a blank template. Do not paste a real key into chat, screenshots, or committed code. Choose Use reviewed corpus and Cited answer in the interface after refreshing the page.

The CLI also reads `.env`:

```sh
.venv/bin/python scripts/ask_parkguide.py "How do I reserve a campground in Yosemite?" --park yose --generate
```

Gemini HTTP 429 or `RESOURCE_EXHAUSTED` errors display exactly **Free quota reached—please try again later.** This is the requested UI wording for quota/rate exhaustion, not proof that the daily allowance alone was exhausted. The app does not automatically retry or switch to another provider. Authentication and other service failures have separate sanitized messages; neither quota nor service failures are counted as evidence refusals.

The chosen model has a published free tier, but actual project limits/access are controlled by Google AI Studio. Google may use free-tier submissions to improve its products; the interface discloses external processing when generation is enabled. See [model pricing](https://ai.google.dev/gemini-api/docs/pricing), [structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output), and [quota troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting). Live key/model access and one validated response are now confirmed. This does not establish semantic correctness or a general success rate.

Generation against pending-review or preview chunks returns `corpus_review_pending` without sending passages externally. A live integration check is recorded in data/audit/gemini_live_check.json; no formal correctness, quality lift, monetary cost, or end-to-end benchmark has been measured.

## Recorded corpus review

2026-10-10: The user reported a basic review of all 420 documents across privacy, relevance, dates, and PDF/table accuracy. Acceptance is recorded in `data/review/corpus_approval.json` with before/after corpus hashes and document identities. The assistant did not independently verify the human review. All four review fields are marked approved on this snapshot; unknown dates remain unknown and are excluded by known-coverage filters.

Chunks were rebuilt from saved normalized files without recrawling. Both the approved and preview indexes were refreshed from that snapshot, replacing stale searchable records. The approved index contains 3,270 fixed-overlap and 5,148 heading-aware chunks. This review acceptance is not evidence of retrieval or generated-answer accuracy. An API key and live generation checks remain outstanding; the human-verified evaluation set is still required.

## AI usage

2026-10-09: Codex read the assignment, researched source suitability, inspected NPS robots rules and sitemaps, and created the source configuration, inventory script, README, and initial proposal. The team has not yet verified gold evaluation items. No performance numbers were generated.

2026-10-09 proposal revision: Codex expanded the proposal against the supplied requirements screenshot, proposed four work roles, checked official technology/model documentation, selected a planned stack, and drafted the architecture, chunk migration, grounding, evaluation, and risk plans. Team assignments and GitHub completion remain unverified; this revision did not run a chatbot or compute performance results.

2026-10-09 crawler implementation: Codex implemented a dependency-free initial crawler and eleven offline tests. The initial crawler uses urllib rather than the proposal's planned Requests client. A live pilot saved 12 responses (six HTML and six PDFs) using the supplied contact email. No chatbot evaluation was performed.

2026-10-09 ingestion expansion: Codex completed the authorized crawl and implemented offline HTML/PDF extraction, metadata, exact-content deduplication, conservative contact redaction, exclusion records, and a review inventory. A discovered clinic staff directory was removed from raw storage and the filename filter tightened. Eighteen automated tests passed; 420 documents were retained by automated extraction. Corpus review and human verification of evaluation questions remain outstanding.

2026-10-09 chunking implementation: Codex implemented both chunkers, pinned the tokenizer revision/checksum, verified source coverage and full-input token budgets, added ten tests, and ran a network-blocked migration demonstration across the whole corpus. Twenty-eight total tests passed. No embeddings, retrieval scores, gold answers, or model performance numbers were generated.

2026-10-10 retrieval implementation: Codex downloaded the pinned embedding model, built local preview vectors and Chroma collections for both strategies, implemented BM25 and reciprocal-rank hybrid retrieval with matching filters, and ran 48 engineering smoke searches. Thirteen retrieval/lifecycle tests brought the total to 41. A full-corpus network-blocked migration removed superseded vector collections and sparse snapshots. Human corpus review and gold-set verification remain pending; no generated answers or formal accuracy/Recall/MRR/nDCG results were produced.

2026-10-10 answering implementation: Codex added the pinned local cross-encoder, pre/post ranking traces, context-only Responses API adapter, citation and quote validation, and refusal gates. Fifteen new tests brought the total to 56. Eight network-blocked engineering probes selected at most five contexts and made zero API generation calls. Human review and gold verification remain pending; no semantic grounding or reranking improvement was established.

2026-10-10 interface implementation: Codex created the local Streamlit chat app, source/filter controls, session history and explicit follow-up selection, clickable source links, expandable ranking traces, review/API gates, and limited emergency referrals. Eleven new tests brought the suite to 66. The browser displayed real local retrieval results; no live generation, human corpus approval, or formal answer evaluation was performed.

2026-10-10 review acceptance: Codex recorded the user-reported all-corpus basic review, preserved source text and known/unknown dates, rebuilt chunk snapshots and both index modes, and enabled the reviewed-corpus interface selection. The local generation gate remains closed while the API key is absent. No live API answers or formal evaluation were performed.

2026-10-10 Gemini setup: Codex added local .env credential loading, switched the configured provider/model to Gemini, retained citation/review gates, and implemented the requested quota message without automatic retries. Six provider/settings tests and one UI quota test brought the suite to 74. The local key entry was blank; no live Gemini request or API performance measurement was made.

2026-10-10 live Gemini verification: Codex checked key presence without displaying it, corrected the REST structured-output MIME enum after HTTP 400 errors, and verified a live generated answer through the citation validator. A prior live response failed evidence validation and was safely refused. These checks are not formal answer evaluation; receipts contain no API key.

2026-10-10 running-server refresh: Codex restarted this project’s localhost server, changed cached engines to reload generation settings for every cited request, and removed a widget initialization warning. A real Streamlit AppTest run with the live Gemini API returned a cited Yosemite answer without a missing-key error; the receipt contains no key. All 16 chat interface/service tests passed after the changes.

## Evaluation review and frozen split

The project owner approved the 40-item AI-assisted evaluation set in [the review packet](reports/evaluation_review.md). The frozen split has 24 development and 16 test items, with related facts grouped and seed 633. Frozen artifacts and approval records are local under `data/evaluation/`. Human scoring of generated answers is separate from gold-answer approval and remains pending. Test questions stay unused until configuration freeze.

Validate offline with `.venv/bin/python scripts/validate_evaluation.py`; add `--require-verified` to reject pending human review. The draft and split manifest live in `data/evaluation/`; [the evaluation report](reports/06_evaluation.md) records the remaining review, freeze and scoring work. No model evaluation calls or performance metrics were produced in this step.

2026-10-10 evaluation drafting: Codex prepared 40 questions from saved source passages, required facts, exact evidence, PDF/table locations, candidate absence-search leads and a family-aware split. Offline positive and negative checks passed; all 40 human reviews remain pending.

## Repository workflow status

The remote is `https://github.com/VishalTalla07/ParkGuideAI.git`. Because the repository initially had no commits, `main` was initialized with a minimal README. Implementation work uses `feature/rag-chatbot`, based on that main commit. The project owner will create the pull request into main. Main branch protection remains unverified; team roles and review assignments still need to be finalized.

## Privacy review update

2026-10-10: All 420 normalized documents received an AI-assisted privacy pattern screen, with contextual checks of flagged contact and medical pages. Two individual directory names were removed while retaining organizations and public business contacts. Local exact-redaction overlays prevent those names returning on extraction. Review packets and rules stay under ignored `data/review/`; retain that local directory when reproducing the reviewed corpus. The chunk and preview index snapshots were refreshed, removed names were verified absent from their source snapshots, and all 67 tests passed. Source dates and extraction/table review remain pending; final human privacy acceptance is recorded separately from screening. No independent human inspection of all pages or absence of every possible identifier is asserted.

2026-10-10 fee refusal repair: Codex diagnosed an overlong model citation quote, clarified generation instructions while retaining strict validation, and verified the reported Yellowstone fee question live. The running app was refreshed and 46 targeted tests passed. Fee accordion extraction was repaired; recovered blocks remain staged for human review and were not added to the approved corpus. No formal evaluation was run.

2026-10-10 gold review completion: The project owner approved all 40 evaluation items in four batches. The verified gold set and 24/16 split are frozen under `data/evaluation/` with corpus and artifact hashes. Model configuration is not yet frozen; no formal evaluation has run. Development work must use only the 24 dev items before the final test stage.


2026-10-10 development evaluation: Codex ran 24 dev queries through six retrieval configurations without network access and computed source-quote-based metrics over 17 ordinary answerable questions. B3 Recall@5=0.8235; complete evidence@5=13/17. Saved evidence mappings, per-item traces, paired exploratory intervals and 15 candidate failure events (not human labels). Six evaluation tests passed. Live B3 generation completed 16 items (ten answered, six refused) before a quota error stopped the batch; eight remain. No test query was run, and no human-scored answer accuracy or billed cost is reported. See `reports/06_evaluation.md` for the review packet and explicit future resume command.
