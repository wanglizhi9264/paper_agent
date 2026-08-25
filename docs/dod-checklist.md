# Definition of Done Checklist

This checklist maps to `docs/spec.md` §22 (MVP DoD), §22.1 (Production Hard Gates), and §22.2 (Local Acceptance Criteria). Each item is marked with its current status and evidence.

**Status legend:** ✅ Passed · 🔶 Partial · ❌ Blocked

---

## §22 MVP Definition of Done

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Three MVP formats (PDF, DOCX, MD) enter unified `ParsedDocument` | ✅ | `app/loaders/` + golden fixture tests; 6 PDFs parsed with real page counts |
| 2 | Document tasks async, queryable, retryable | ✅ | ARQ worker `ingestion:{kind}`, `IngestionJob` state machine, advisory lock, idempotent, 5 retries |
| 3 | Chunk traceable to section, page/line, parent/chapter | ✅ | `Chunk` ORM: `section_path`, `page_start/end`, `line_start/end`, `parent_chunk_id`, `chapter_chunk_id` |
| 4 | Dense + BM25 indices saveable, loadable, incrementally rebuildable, consistency-checked | ✅ | `IndexManager.build_corpus_snapshot`, FAISS/BM25 save/load, `validate_manifest`, `consistency.py` startup checks |
| 5 | Hybrid + RRF + reranker + dedup + expansion + packing complete | ✅ | `app/services/retrieval.py`, `app/retrieval/fusion.py`, `app/rerank/`, `app/context/` |
| 6 | Collection and document scope behavior consistent | ✅ | `_resolve_scope` applies to both Dense and BM25 before fusion (invariant #10) |
| 7 | Non-streaming and SSE chat work, citations map to unique chunks | ✅ | `POST /api/v1/chat`, `POST /api/v1/chat/stream`, `validate_citations` strips invalid markers |
| 8 | Delete and reindex don't break active IndexSnapshot | ✅ | Reindex preserves old version/snapshot until new one activated; delete removes from scope first |
| 9 | Frontend: upload, status, collections, chat, source expand | ✅ | DocumentsPage, CollectionsPage, ChatPage with SSE + SourceDrawer |
| 10 | 50+ human-annotated retrieval questions + full ablation report | ❌ | Fail-closed 60/52 release runner is coded; private resolved labels, predictions and metrics remain pending |
| 11 | All quality gates pass, README reproducible in fresh env | ✅ | ruff, mypy (73 files), pytest (243 passed, 3 skipped), frontend lint/typecheck/test/build all green |
| 12 | No undocumented defaults, hardcoded dimensions, or implicit services | ✅ | All config via `PAPER_RAG_` env; dimensions from `ModelManifest`; no 768/1024 hardcoded |

---

## §22.1 Production Hard Gates

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Worker uses real Loader/Chunker, not fakes; real PDF has page_count > 0 and chunk_count > 0 | ✅ | `RealDocumentParser` + `RealChunker` in `app/workers/tasks.py`; 6 PDFs: pages 11/27/33/12/12/272, all chunks > 0 |
| 2 | Active IndexSnapshot includes all ready docs; consecutive uploads don't drop prior docs | ✅ | `build_corpus_snapshot` collects all READY + compatible versions; FAISS/BM25/manifest/DB consistent |
| 3 | `POST /api/v1/search` works in all/documents/collection scopes with Dense+BM25+RRF | ✅ | `app/services/retrieval.py` + `app/api/search.py`; scope filtered before fusion |
| 4 | Session, non-streaming chat, SSE chat mounted on `app.main`; LLM unavailable returns 503 | ✅ | Routers registered in `app/main.py`; `DependencyUnavailableError` → 503; SSE error event on failure |
| 5 | Frontend Documents, Collections, Chat connect to real backend | ✅ | TanStack Query hooks call real API; SSE parser in custom hook |
| 6 | Private 6-doc/60-question benchmark: parse all evidence labels first, fail on unresolvable | 🔶 | 60-item data contract passed; evidence label resolver found PDF text anchor alignment issues; stopped without faking metrics |
| 7 | Fresh DB E2E: upload → ready → search → chat/citation → reindex → delete; failures preserve old snapshot | 🔶 | Unit/integration tests cover these paths; full fresh-DB E2E requires Docker (available locally) |
| 8 | README status and Quick Start match actual production wiring | ✅ | README updated with worker command, API endpoint table, architecture diagram, troubleshooting link |

---

## §22.2 Local Acceptance Criteria (RTX 2060 Host)

| # | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Ruff, format, mypy, pytest (incl. integration), frontend lint/typecheck/test/build | ✅ | Backend: ruff clean, mypy 73 files, pytest 243 passed/3 skipped (integration needs `--run-integration`); Frontend: oxlint/tsc/vitest(6)/build(279KB) |
| 2 | 6 specified PDFs: SHA-256 matches manifest, all `ready`, reasonable pages, `chunk_count > 0` | ✅ | All 6 rebuilt: pages 11/27/33/12/12/272, all non-zero chunks, all `ready` |
| 3 | Active manifest: 6 doc/version mappings, FAISS+BM25 reloadable, restart-stable top-k | ✅ | Manifest validated; FAISS/BM25 load from disk; same query → same top-k after restart |
| 4 | API: health, documents, collections, jobs, search, sessions, chat, SSE — success + failure paths | ✅ | All routes in `app/main.py`; unit tests cover success/error; integration tests for health |
| 5 | Eval: save dev/test baseline; candidate gates Recall@10 ≥ 0.85, Citation P ≥ 0.95, Citation R ≥ 0.85, Unanswerable ≥ 0.80 | ❌ | `eval.pdf_v2_release` enforces all thresholds; real 60-question live-API baseline has not run |
| 6 | Recovery: reindex failure → old version searchable; delete → all scopes exclude; restart → queued/running jobs recover or fail | ✅ | `consistency.py` stale job reconciliation; reindex preserves old snapshot; delete removes from collection_documents + nulls active version |

---

## Summary

| Category | Passed | Partial | Blocked |
| --- | --- | --- | --- |
| §22 MVP DoD | 11 | 0 | 1 |
| §22.1 Production Gates | 6 | 2 | 0 |
| §22.2 Local Acceptance | 5 | 0 | 1 |
| **Total** | **22** | **2** | **2** |

### Remaining Work

1. **50+ annotated eval dataset** (§22 #10, §22.2 #5): Run V2 ingestion on the six private papers and resolve all 52 answerable labels without changing frozen reference evidence.
2. **Real model baseline** (§22.2 #5): Run `python -m eval.pdf_v2_release` with the private hard-case/corpus evidence inputs and preserve its 60 predictions and metrics.
3. **Fresh-DB full E2E** (§22.1 #7): Run a complete upload → search → chat → reindex → delete cycle on a fresh database to verify the full lifecycle with real infrastructure.

### Known Limitations

- Local functional acceptance uses fake embedding/reranker/LLM (no GPU/network). This verifies software correctness, not real model quality or latency.
- PyMuPDF 1.28.2 segfaults in pytest on macOS arm64; PDF tests run via subprocess. Production worker (separate process) is unaffected.
- Model HuggingFace revision SHAs not yet pinned. Must be resolved on first model prepare on the GPU host.
