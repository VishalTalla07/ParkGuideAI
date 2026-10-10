# ParkGuide AI — Embeddings and Vector Store

Date: October 10, 2026

## Implemented status

The local development preview contains two Chroma collections over the same 420-document corpus: 3,270 fixed-overlap vectors and 5,148 heading-aware vectors. Review metadata remains pending; the approved builder rejects the current corpus. No source text was sent to a generation API.

## Model and input contract

Model: `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The download manifest records checksums for weights, tokenizer, pooling, and configuration files. Model loading uses local files and disables remote model code. The tokenizer checksum must match the pinned chunking tokenizer.

Sentence Transformers 5.2.0 with Torch 2.10.0 computes 384-dimensional CPU embeddings. Complete inputs include park/heading context and body text. Both document inputs and queries are checked before encoding against the 256-token limit; overlong inputs are rejected. L2 normalization is enabled and every output is checked for dimensions, finite values, and unit norm.

Chroma 1.5.5 uses explicit cosine-space configuration and supplied embeddings; its automatic embedding function is disabled. The collection contains chunk IDs, vectors, and scalar filter metadata. Full text, detailed provenance, and review status are kept in versioned JSONL records.

Implementation references: [SentenceTransformer API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html) and [Chroma collection API](https://docs.trychroma.com/reference/python/collection).

## Observed vector checks

| Strategy | Vectors | Source documents | Dimensions | Minimum L2 norm | Maximum L2 norm | Finite values |
|---|---:|---:|---:|---:|---:|---|
| fixed_overlap | 3270 | 420 | 384 | 0.999999821 | 1.000000119 | True |
| heading_aware | 5148 | 420 | 384 | 0.999999821 | 1.000000119 | True |

The initial preview build took 41.12 seconds on the local CPU, with four Torch threads. This measurement starts after model loading and includes embedding/cache/index work; it is not end-to-end query latency. Restoring the default index reused cached vectors. Floating-point norms vary slightly around one and passed the 1e-5 tolerance.

## IDs, caching, and persistence

Each strategy uses the deterministic chunk IDs from its manifest. The vector collection, sparse/provenance snapshot, and expected ID set must agree exactly. The index identity includes chunk snapshot, model revision/file hashes, runtime model-library versions, retrieval configuration, and review namespace. Query models/configuration must match the indexed identity.

Validated vectors are cached by model identity and embedding-input SHA-256. Cache entries contain vectors only and are not searchable chunk records. Persistent data under data/index/ and downloaded weights under data/models/ are excluded from Git. Re-running the downloader verifies existing file hashes; installing libraries and downloading weights require network access once. Indexing/query execution use local files.

## Lifecycle checks

New collections and sparse/provenance files are staged and validated before an atomic active-pointer switch. Superseded collections and sparse versions are deleted afterwards. Failed activation preserves the previous active index and removes the replacement. Retry clears abandoned staging collections. Locks protect builders and readers; a stale retriever must reload after migration.

The full-corpus demonstration rebuilt 200 → 160 → 200 token chunks with sockets blocked. It restored index `6c394e466fe087c9620e5e20f32927d5add51533cdec85607c644ac48a7ed1f5`, removed 10,048 old-only IDs and both alternate vector collections, and verified zero orphan vector collections/sparse versions. No re-crawl occurred. The same normalized corpus was used throughout.

## Reproduce

```sh
.venv/bin/python -m pip install -r requirements.txt
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python scripts/download_embedding_model.py
.venv/bin/python scripts/build_index.py --development-preview
.venv/bin/python scripts/demo_index_migration.py
```

Configuration: config/retrieval.json. Initial build checks: data/audit/retrieval_preview_summary.json. Migration receipt: data/audit/index_migration_demo.json. Cached restore/build receipts: data/audit/index_runs/.

## Remaining work

Corpus privacy/relevance/date review and the 40 human-verified evaluation questions remain pending. The current index is an engineering preview. Reranking, grounded generation, refusals, the UI, and formal retrieval/answer evaluation remain to be implemented. No accuracy, Recall, MRR, or nDCG values are claimed.

AI assistance: Codex implemented and tested the local embeddings/vector lifecycle and ran this preview and migration demonstration.
