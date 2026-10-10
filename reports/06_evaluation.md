# Evaluation plan and draft status

The AI-assisted source-backed gold set contains 40 items. The project owner explicitly approved all 40 in four review batches on 2026-10-10; none remain pending. The gold set and 24/16 split are frozen, while model, prompts and retrieval configuration remain open for development work. Development retrieval evaluation has run and 16 B3 generation requests completed before quota exhaustion. Answer correctness has not been human-scored; no test-set result is claimed. Corpus review approval does not verify gold answers. The corpus byte fingerprint and per-source content hashes bind this draft to the saved snapshot.

| Category | Total | Dev | Test |
|---|---:|---:|---:|
| Single-hop factual | 15 | 9 | 6 |
| Multi-hop, at least two pages | 6 | 4 | 2 |
| Comparative | 4 | 2 | 2 |
| Temporal | 4 | 2 | 2 |
| Unanswerable | 8 | 5 | 3 |
| Ambiguous/adversarial | 3 | 2 | 1 |
| Total | 40 | 24 | 16 |

## Review and freeze

Read `reports/evaluation_review.md` against the saved `data/normalized/documents.jsonl` blocks, rather than assuming today's live website matches the snapshot. All expected facts must be supported and complete. Validate temporal interpretation, table headers and PDF extraction, and whether both sources are necessary for each multi-hop item. Confirm that proposed refusal items lack sufficient context after a full corpus search; keyword hit counts are only search leads. The ambiguous fee question must request clarification. The adversarial questions must not fabricate facts or disclose credentials.

Record corrections and a human reviewer/date for every item. Only a human's explicit approval permits `review.status=approved`; do not infer approval from the earlier corpus review. Replace defective items while retaining quotas, then recompute related families and the proposed split if necessary. Review overlapping facts beyond the automated shared-unit grouping. The split uses seed 633, a seeded family shuffle, and exact-quota backtracking; identical evidence units remain together, as do Canyon schedule/fee questions. The split is a draft until review is complete.

Run `.venv/bin/python scripts/validate_evaluation.py` for offline source and split checks. `--require-verified` additionally rejects any item without approval, reviewer and date. This verifies annotation structure and exact excerpts, not semantic correctness or genuine human identity. A future evaluation runner must invoke this gate before any model call; no evaluation runner is implemented in this step.

After all approvals, freeze hashes of the gold set, corpus, split, prompts, model and retrieval configuration. Tune only on the 24 development items. Prior engineering probes and live UI smoke checks are not gold evaluation results. Avoid using related smoke-probe questions as unseen test claims. The final 16-item test run occurs after configuration freeze, once for each predeclared configuration; do not retune after viewing results.

## Baselines and measures

- B0: same LLM, no retrieved corpus context, closed-book answer with uncertainty allowed.
- B1: BM25 over fixed-overlap chunks; top five; no reranker.
- B2: dense cosine over fixed-overlap chunks; top five; no reranker.
- B3: heading-aware chunks, hybrid retrieval, cross-encoder reranking, grounded generation.

Map each human-approved evidence unit to both chunk strategies before measuring retrieval. Report evidence-unit Recall@5 and Recall@20, MRR, binary nDCG@10 without repeated-unit credit, multi-hop all-source coverage, and reranker candidate recall. Exclude refusal items from retrieval denominators. Required evidence mapping and metric computation remain unfinished.

Human answer scoring requires every requested fact without unsupported additions. Separately score claim-level citation presence, eligible URL and semantic support, correct refusals over the eight unanswerable items, over-refusal over answerable items, and clarification/adversarial behavior. API quota failures are operational errors, not correct refusals. Report actual denominators and undefined rates explicitly.

Report B0 correctness and answer accuracy on the answerable B0-incorrect subset, with its denominator. Compare chunking, dense versus hybrid retrieval, and reranking one factor at a time while keeping generation settings fixed. Record stage timing, p50/p95 latency, tokens and actual billed API cost; do not invent dollar costs or equate a free tier with unlimited availability.

Use paired category-stratified bootstrap confidence intervals with 10,000 resamples and saved seed 633. Small test counts limit precision. Human-label at least 15 observed failures across retrieval misses, rank failures, chunk boundaries, extraction errors, stale content, reranker demotion, generation ignoring context, invalid citations, over-refusal and hallucination. Additional diagnostic items remain separate from headline results.

## Current validation

All 40 draft items have valid source identities, exact evidence excerpts of at most 25 words where answers need support, and the specified category/split counts. All six multi-hop items reference distinct pages. PDF page and table provenance pass structural checks. Negative checks reject fabricated excerpts, changed corpus fingerprints, crossed question families and all 40 unreviewed items. These are offline annotation checks with zero model calls, not performance measurements.

## Human review progress

2026-10-10: Recorded explicit user-reported approval of PG-001–PG-010, with item hashes and the corpus fingerprint in `data/evaluation/approval_batch_001_010.json`. No questions, answers, evidence or splits were changed. All 30 remaining items stay pending; the whole-set verification gate remains closed.

2026-10-10: Recorded user approval of the requested PG-011–PG-020 batch. The approval record preserves item hashes and corpus identity. Current total: 20 approved, 20 pending. Source facts and splits were not modified; no evaluation model calls were made.

2026-10-10: Recorded user approval of the requested PG-021–PG-030 batch. The approval record preserves item hashes and corpus identity. Current total: 30 approved, 10 pending. Source facts and splits were not modified; no evaluation model calls were made.

2026-10-10: Recorded approval for PG-031–PG-040. All 40 pass the human-verification metadata gate, exact evidence/source checks, category quotas, family grouping and approval hash checks. Saved frozen `golden_set.jsonl`, `split.json`, and `freeze_manifest.json` without changing facts or split assignments. No model scoring has run. Next: implement evidence-to-chunk mapping and baseline/metric tooling, work only on dev, and reserve test until configuration freeze.


## Development run 1 (2026-10-10)

Ran all 24 development questions against six local retrieval configurations with network calls blocked: B1 fixed/BM25, B2 fixed/dense, A1 heading/dense, A2 heading/hybrid, B3 heading/hybrid/reranked, and heading/BM25 as a diagnostic. No park/type/date filters were derived from gold labels. Mapped each dev evidence unit to both strategies by source ID, content hash, exact excerpt and PDF page. All units map to at least one chunk. The 16 test questions were not executed or mapped for retrieval scoring.

Headline macro metrics use the 17 ordinary answerable dev questions (9 single-hop, 4 multi-hop, 2 comparative, 2 temporal). The five unanswerable and two ambiguous/adversarial questions remain outside headline retrieval denominators. B3 Recall@5 is 0.8235 and Recall@20 is 0.9412; complete approved evidence appears in the top five for 13/17, and all required source pages for 2/4 multi-hop questions. MRR@20 is 0.7255 and binary novel-evidence nDCG@10 is 0.7635. These are quote-mapping retrieval measures, not answer correctness; alternative semantically sufficient evidence may not match the annotated excerpts.

See `data/evaluation/dev_retrieval_v1/retrieval_report.md` and `summary.json` for all baseline/ablation values. B0 has no retrieval metrics and remains unrun. Saved rankings, exact generation inputs, configurations, index/code/corpus hashes and evidence mappings allow inspection of each scored case. Query timing is retrieval plus reranking only, excludes model loading and UI, and reuses the A2 search for B3. It is not end-to-end chat latency.

Exploratory paired category-stratified bootstrap intervals use 10,000 resamples with seed 633; exact McNemar results are also saved. B3 versus A2 Recall@5 difference is 0.0588, with bootstrap interval [-0.0588, 0.2059]. Small categories, related question families and repeated dev use limit inference; this does not establish a reranking improvement. A review packet contains 15 candidate failure events across 12 distinct questions and configurations; zero have been human-labeled. No final 15-failure labeling requirement is claimed complete.

### Partial B3 generated answer collection

The approved Gemini free-tier key was used sequentially with no automatic retries. Sixteen requests completed (ten answered, six refused); the seventeenth attempt, PG-029, returned quota exhaustion. The batch stopped immediately. Eight IDs remain: PG-029, PG-030, PG-032, PG-033, PG-035, PG-036, PG-038 and PG-039. Quota failure is an operational error, not a correct refusal. No answer accuracy, semantic citation score, refusal rate or over-refusal rate has been assigned. In particular, an `answered` status only passes structural citation checks.

Review saved answers and their expected facts in `data/evaluation/dev_answers_b3_v1/answer_review.md`. Provider usage totals 25,571 tokens for completed responses; actual billed cost is unknown. The recorded pipeline p50/p95 sums cached retrieval/reranking timings and live generation timings and excludes UI/model load. This is not a measured interactive user latency.

After quota becomes available, explicitly resume only unfinished items, preserving completed answers:

```bash
.venv/bin/python scripts/generate_dev.py --resume-from data/evaluation/dev_answers_b3_v1 --output data/evaluation/dev_answers_b3_v2
```

This is a manual future command, not a scheduled retry. The runner verifies frozen gold/split/corpus, approval hashes, query IDs, current index, provider settings and relevant code before API calls. A resume checks the prior run identity and skips completed answers. Subsequent B1/B2 dev answer runs need separate versioned outputs; B0 closed-book generation still needs an adapter. Test generation remains outside these dev-only tools.

Validation: six meaningful evaluation tests pass for duplicate evidence, partial multi-hop coverage, achievable nDCG ideals, unscorable items, source/hash/page identity, and correct headline denominators. Retrieval/generation/analysis scripts compile. Frozen files and the 40 human approvals remain unchanged.
