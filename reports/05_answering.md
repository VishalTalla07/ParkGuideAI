# Reranking and grounded answer design

## Implemented milestone

ParkGuide AI now reranks the heading-aware hybrid retrieval pool with a local cross-encoder and supplies at most five passages to an answer adapter. Generation was initially implemented with the proposal's pinned OpenAI model. The active provider is now Gemini, as described in the update below. No live generation call has been made: the user-reported basic corpus review is recorded, but an OpenAI API key has not been configured.

The CLI can display the reranked evidence locally. It labels this as an evidence preview rather than an answer. The chat interface is now implemented as described below. The user-reported all-corpus basic review is now accepted; human gold evaluation remains outstanding.

## Reranking

The model is `cross-encoder/ms-marco-MiniLM-L6-v2`, pinned at revision `233902d25c440f23af6f7d6e94d2946bac0bee0a`. Only six configuration/tokenizer/safetensors files are downloaded; local loading checks all recorded SHA-256 hashes and disables remote model code. Inference runs on CPU with four Torch threads. Each input is the question paired with the chunk's heading-prefixed embedding input. Every pair is checked against the 512-token budget before inference; overlong queries fail explicitly rather than silently losing text.

The reranker scores every available member of the retrieval pool, up to 20 unique chunks, then sorts raw logits in descending order with chunk ID tie-breaking. It retains the full reranked pool and at most five context chunks. Each entry preserves the original dense/BM25/RRF scores, pre-rank, post-rank, and cross-encoder score. Scores are ordering signals, not calibrated probabilities. No score threshold has been tuned.

This follows the [Sentence Transformers CrossEncoder API](https://www.sbert.net/docs/package_reference/cross_encoder/model.html) and the [published model repository](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2).

## Generation and refusal

The adapter sends only the current question and selected source passages, with source identity and coverage metadata. A system instruction treats both user text and passages as untrusted data and directs the model to use only evidence, ignore embedded instructions, paraphrase, and refuse essential gaps, ambiguity, conflicts, or unsupported date validity. It prohibits personalized medical advice and emergency instructions. Prompting alone cannot guarantee resistance to injection or factual grounding.

A strict JSON schema requires either an answer consisting of claims with evidence items, or a refusal with no claims. Every evidence item contains an allowed chunk ID and an exact supporting quote of at most 25 whitespace-delimited words. The application constructs clickable citations from stored NPS URLs; the model cannot supply its own citation URL. The selected model supports the Responses API and structured outputs. See the [model documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini) and [structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

The local validator fails closed on missing citations, unknown chunk IDs, quotes absent from source text, invalid or external source URLs, malformed outputs, incomplete responses, and model refusals. It checks normalized 26-word sequences across all displayed claims against the supplied sources to reject long copied runs. This conservative lexical check can flag common wording; quote provenance does not prove semantic support. A model can attach a real quote to an unsupported claim, or combine several facts into one sentence. Human claim-level evaluation and adversarial tests are required before claiming reliable grounding. There is no separate entailment verifier yet.

Empty context yields an explicit refusal without calling an API. Pending privacy/temporal review or a development-preview index also prevents API transmission. Approved generation requires an approved index, `ready_for_index=true`, and both review statuses approved on every selected chunk. The API key is read only from `OPENAI_API_KEY`; it is never written to traces or printed. Requests use `store=false`, HTTPS, a 60-second timeout, and an output-token cap of 1,600. Transport failures raise an error rather than being mislabeled as evidence insufficiency. `store=false` is not a claim of zero provider retention.

Successful API responses record reported token usage and generation latency. No API usage, monetary cost, or live answer quality has been measured. Conversation history, limited high-stakes referrals, and expandable traces are implemented in the UI below.

## Data flow

Question and explicit park/page/date filters → heading-aware hybrid retrieval → at most 20 candidates → cross-encoder scores → at most five contexts → corpus review gate → context-only structured generation → local citation/quote/copy validation → cited claims or explicit refusal. The trace includes the pre/post ranking stages and separate retrieval, reranking, and generation timings.

## Verification and limitations

The full test suite passed 56 tests, including 15 new tests for citation construction, missing/fabricated evidence, URL validation, copy limits spanning claims, corpus-review gates, empty context, structured requests, incomplete/model refusals, top-five selection, score preservation, and pair token-budget rejection. Generation tests use synthetic responses; they are not live LLM evaluations or evidence of prompt-injection robustness.

Eight real-corpus engineering probes ran with network sockets blocked during search, reranking, and generation gates. The probes preserved candidate identity, selected at most five chunks, and made zero live API calls. The unknown-period filter returned no contexts; remaining preview probes were prevented from generating by the review gate. CPU reranking-only p50 was approximately 84 ms and p95 100 ms over the seven nonempty pools. This tiny sample excludes model loading, retrieval, generation, and UI and is not an end-to-end benchmark. The receipt is `data/audit/answering_smoke.json`.

No Recall@20 ceiling, MRR, nDCG, reranking quality lift, correctness, citation-validity rate, or refusal rate is reported. Those require the human-verified 40-item golden set, frozen split, and baseline runs. Corpus approval remains pending; all current behavior is an engineering preview.

## Reproduction

```sh
SSL_CERT_FILE=/etc/ssl/cert.pem .venv/bin/python scripts/download_reranker.py
.venv/bin/python scripts/ask_parkguide.py "How do I reserve a campground in Yosemite?" --park yose --development-preview --json-output tmp/question_trace.json
.venv/bin/python scripts/smoke_answering.py
.venv/bin/python -m unittest discover -s tests -v
```

After human corpus approval and rebuilding an approved index, set `OPENAI_API_KEY` securely in the local terminal environment and use `--generate` without `--development-preview`. Do not paste credentials in a chat or commit them. Live API access must still be tested before claiming the answer path works end to end.

## Streamlit chat interface

`app.py` uses Streamlit 1.65.0, pinned with its dependencies in `requirements.txt`. Launch with `.venv/bin/python -m streamlit run app.py`, then open http://127.0.0.1:8501. The configuration binds to localhost, uses a light green theme, and disables Streamlit usage telemetry. It is a local development app without production authentication or deployment.

The interface includes a chat input, three example questions, Yosemite/Yellowstone/Grand Canyon filters, HTML/PDF filters, optional known-coverage date filtering, and separate Source preview/Cited answer response modes. Source preview uses the current development corpus; cited generation retains all existing review gates. The reviewed-corpus selector is disabled until an approved index exists. API keys are read from the process environment, never entered or displayed in the UI. An external-processing notice is shown when approved live generation is available.

Each browser session has its own in-memory history. Clear conversation deletes that session's displayed turns. A follow-up explicitly selects one earlier user question; that question and the new question are used for retrieval and generation. Prior assistant text is never added as source evidence. The current filter controls apply to the new request and are recorded with its reply. History is not written to disk, and it is not an automatic conversation summarizer. Long combined questions can exceed the embedding token budget and receive a shortening prompt. A conservative pronoun pattern asks users to select context for ambiguous follow-ups; false positives and missed ambiguity are possible.

Preview replies show at most five selected passages, each shortened to 25 whitespace-delimited words, clickable allowlisted NPS links, PDF page references where present, and source update dates explicitly labeled as insufficient to establish current validity. Generated claims use the adapter's source citations. Refusals appear as visible warning messages with a reason and possible recovery action; service errors are distinguished from evidence insufficiency, sanitized, and kept in history. This does not establish semantic correctness.

Expandable traces show all candidate pre/post ranks, dense/BM25/RRF and cross-encoder scores, selected flags, pool shortfalls, separate stage latency, filters, index/model identities, and model token usage when available. Full JSON traces can be downloaded explicitly to the user's local computer. These include the user's question and selected earlier question; there is no automatic trace persistence. Stage timings exclude model loading and are not formal end-to-end measurements. Shared cached engines use a lock for model execution; the active index version participates in the cache key so subsequent requests load a migrated index.

A narrow keyword/pattern screen routes emergency and personal medical questions to emergency services or a park ranger without retrieval/generation. This screen is not a medical classifier and needs broader adversarial/human evaluation. The interface identifies ParkGuide AI as a student project independent of the NPS and advises confirming time-sensitive plans with the park.

Implementation follows the official [Streamlit chat input](https://docs.streamlit.io/develop/api-reference/chat/st.chat_input), [resource caching](https://docs.streamlit.io/develop/api-reference/caching-and-state/st.cache_resource), and [AppTest documentation](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).

Eleven additional tests cover empty results, filter/follow-up forwarding, ambiguous follow-ups, emergency referrals including selected context, URL/error sanitization, chat history, clearing conversation, response-mode forwarding, separate sessions, and error display. The full suite passed 67 tests and dependency checks found no broken requirements. UI tests use a synthetic engine; separate live browser verification retrieved and reranked a Yosemite camping question, displayed five NPS source links, and expanded the full-pool ranking trace. Cited-answer preview testing displayed the pending-review gate with no live generation. Generated-answer UI behavior is not yet verified against a live model.

## Corpus acceptance update — 2026-10-10

The project owner reported completing a basic review of all 420 documents across privacy, relevance, dates, and PDF/table accuracy. `data/review/corpus_approval.json` records this user-reported acceptance against the exact normalized snapshot and individual document IDs. The assistant has not independently verified the human inspection. Privacy and temporal approvals permit index activation; relevance and extraction approvals are also recorded on the documents. Unknown coverage metadata remains unknown, without certifying current conditions.

Both chunk strategies were rebuilt from the saved documents without recrawling, and the approved and development indexes were refreshed. The approved snapshot contains 8,418 chunks (3,270 fixed and 5,148 heading-aware). The interface selects the reviewed corpus when it first becomes available; source preview can use it without an API key. Earlier pending-review descriptions above document the prior engineering milestone.

Verification checks the reviewed flags, index manifests/IDs in both modes, approved retrieval, and the missing-key generation gate with network access blocked. The receipt is `data/audit/corpus_approval_verification.json`. No live generation or answer-quality evaluation was performed. The next prerequisite for live answers is a locally configured API key, followed by an end-to-end generation check. Review acceptance does not replace the human-verified 40-question evaluation set.

## Gemini provider and local key setup — 2026-10-10

At the user's request, the active configuration now selects Google Gemini `gemini-3.5-flash-lite`. The official model/pricing documentation lists a free tier and structured output support. New-project access to older 2.5 models is restricted, so the current configuration uses a model documented for new projects. This model name is an alias; successful responses record the returned `modelVersion` for later reproducibility. See [models](https://ai.google.dev/gemini-api/docs/models), [pricing](https://ai.google.dev/gemini-api/docs/pricing), and [structured outputs](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).

The adapter calls the HTTPS generateContent endpoint with the key in the `x-goog-api-key` header, never the URL. It supplies the same context-only system instruction, current question, and at most five approved passages. `generationConfig.responseFormat.text` contains the application JSON schema and JSON MIME type; successful STOP candidates pass through the existing local claim/citation validator. Incomplete, blocked, malformed, or fabricated-evidence responses fail closed. No web grounding tools are enabled. Output is capped at 1,600 tokens. TLS verification uses the project's pinned certifi CA bundle. The OpenAI adapter remains available for historical reproducibility but is not selected by the current configuration.

Credentials come from `GEMINI_API_KEY` in the process environment or the root `.env` file, with a nonempty process value taking precedence. The existing `.env` contained a blank entry; it was preserved and given owner-only filesystem permissions. `.env.example` contains a blank template, and Git ignore checks confirm `.env` is excluded. Keys are never entered in the chatbot, logged, or included in traces. The sidebar reports only local key availability, which does not certify that the key is valid. Refreshing the app reads the saved file; model configuration changes invalidate the cached engine via a nonsecret configuration signature.

HTTP 429 or RESOURCE_EXHAUSTED becomes the exact user-requested error message: **Free quota reached—please try again later.** The app makes no automatic retry or provider fallback. This wording covers quota/rate exhaustion; it does not identify which specific limit was reached. The message is an operational error, distinct from unsupported-question refusals, and available retrieval traces remain attached when the service catches the quota exception. Other authentication/service errors are sanitized separately. See [Google's troubleshooting guide](https://ai.google.dev/gemini-api/docs/troubleshooting).

The UI discloses that questions, selected prior questions, and passages are sent to Google Gemini and that free-tier submissions may be used for product improvement. Review approval is still required before transmitting source passages. The local key was blank during implementation, so no live API generation, account quota, cost, semantic accuracy, or model access was measured.

Six new tests cover Gemini schema requests and validated responses, fabricated evidence, incomplete/blocked outputs, HTTP and structured quota errors without retries or keys in URLs, and .env/environment precedence. An additional Streamlit UI test verifies the exact quota message. The full suite passed 74 tests. Existing OpenAI synthetic tests continue to verify the earlier adapter independently.

## Live Gemini integration check — 2026-10-10

After the user added the key locally, the key loader confirmed availability without displaying it. Initial requests returned HTTP 400 because the REST responseFormat MIME field requires the enum `APPLICATION_JSON`, rather than the string `application/json` shown in a guide example. The adapter and regression test now use the enum documented in the [REST TextResponseFormat reference](https://ai.google.dev/api/generate-content#TextResponseFormat).

The corrected request reached Gemini successfully. One generated answer was rejected by the citation validator with `invalid_evidence`; a subsequent diagnostic response for the same public Yosemite camping question passed the validator. Its two evidence items referenced known chunk IDs and used 17-word and nine-word quotes. This confirms a live API/generation/validation path, not semantic accuracy or a general success rate. The initial failure and successful follow-up are recorded in `data/audit/gemini_live_check.json`. Earlier statements that the key was blank or no live call had been made describe the prior milestone. Quota-message tests passed offline; quota exhaustion has not been induced on the user's account.

## Response-mode default correction — 2026-10-10

The initial interface always defaulted to Source preview, which persisted after configuring the key. It now switches to Cited answer when the selected reviewed corpus and a local key become available, including existing preview sessions. A later explicit preview selection is preserved while readiness is unchanged. Previous conversation entries keep their original response; users must ask a new question after the mode changes. Three additional AppTest checks cover ready-mode defaults, preserving a manual preview choice, and switching an existing session when a key is added. All 15 chat interface/service tests passed with synthetic engines and no live API calls.

## Running-server credential refresh — 2026-10-10

The local key loader found the configured Gemini key, but the user's running app still reported a missing key. The project's localhost server was restarted to clear previously loaded modules/resources. Cached engines now reload generation settings on every cited-answer request before selecting the credential variable, with a regression test for an engine initialized with an earlier OpenAI configuration. A widget initialization warning was also removed.

A real Streamlit AppTest run using local models, the approved index, and the live Gemini API selected Cited answer and answered a public Yosemite tent-only campsite question with an NPS citation. The missing-key error was absent. Its receipt is `data/audit/server_refresh_check.json`; no key value appears in the receipt. This verifies the framework/API path rather than the user's existing browser history, and is not formal accuracy evaluation. All 16 chat interface/service tests passed offline after the changes. The server is running again on the same localhost port; refreshing the browser reconnects to it, and the prior in-memory history is reset.


## Yellowstone fee refusal repair (2026-10-10)

A user-reported entrance-fee question retrieved the correct approved Yellowstone fee page, but live Gemini output included a 34-word evidence quote. The unchanged 25-word validator correctly rejected it as `invalid_evidence`; the UI previously presented that as generic insufficient source information. Generation instructions and the evidence schema description now explicitly require short contiguous quotes, with multiple evidence items for long supporting sentences. The UI distinguishes citation failures from insufficient context. No automatic retries were added.

A live rerun of the same diagnostic question passed citation validation and returned the standard fee range and conditional nonresident surcharge from the saved source. This is one engineering check, not a formal accuracy result or a guarantee of future model behavior. Receipts are `data/audit/yellowstone_fees_live_debug.json`, `yellowstone_fees_model_output.json`, and `yellowstone_fees_fixed_check.json`; none contains an API key. The localhost Streamlit server was restarted with the change.

The extractor also omitted labels and prices stored inside NPS fee accordion buttons. It now preserves only their semantic header text before removing generic controls. Newly recovered blocks from three saved fee pages are staged in `data/review/fee_card_extraction_candidates.json`, pending human review; the approved corpus, chunks, indexes, evaluation questions and ten item approvals remain intact. Future extraction snapshots must still undergo review. Adding those blocks to the active corpus will require a reviewed migration and revalidation of the evaluation snapshot.

Validation: 16 answering, 6 Gemini, 16 interface/service, and 8 extraction tests passed (46 targeted tests). The regression verifies that an overlong fee quote is rejected while two short exact supporting excerpts pass. The extraction regression verifies fee label/price preservation, heading provenance and removal of unrelated buttons. Offline evaluation validation still reports 40 items, ten approvals, and the unchanged 24/16 split.
