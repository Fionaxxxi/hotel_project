# G4 AI advisory contract v1

Actual provider: local ONNX quantized `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, Apache-2.0. Mean pooling over attention mask followed by L2 normalization; 128 tokens, two CPU threads. Only hotel ranking uses the model; room/rate suggestions are explicitly RULE_FALLBACK. No generation prompt or business tools.

`POST /v4/suggestions` freezes tenant-scoped source/facts/staging reference and a maximum of ten current candidate versions. NFKC rule retrieval blocks country/city mismatches; model reranks this package, not the entire hotel database. Candidates rejected for the same facts/target version are excluded using append-only feedback. Rejection changes the package hash; facts/target changes permit reassessment.

The input hash includes tenant, source, candidate package, exact model revision, adapter and rules. Results are historical evidence, not current mapping facts. Numerical output must contain exactly one finite cosine score per frozen candidate; evidence is assembled from server-frozen source fields. Public callers cannot supply model output or an arbitrary model URL.

AI_AVAILABLE, RULE_FALLBACK and STALE are distinct. The isolated subprocess receives only texts, not DB credentials or channel tokens. Global database advisory runtime permit serializes concurrent AI workers; leased jobs validate owner, fence and lease on result commit. A load timeout is 120 seconds; hot response timeout 15 seconds terminates the model process. A claim allows at most two actual attempts per budget cycle; completed fallback is retained, not silently retried forever. An operator can explicitly reopen a finite budget with a reason, up to three cycles; attempts remain append-only. Defaults are deliberately conservative and need production calibration.

The model never approves a mapping or snapshot. `/v1/mappings/.../decisions` still enforces tenant, role, CAS and deterministic conflicts. Similarity is not probability. Source text renders as text; no remote code, external inference or untrusted HTML.

Preparation is separate from startup: `g4.ps1 prepare-model`. Missing weights and disabled models preserve rule/manual workflows. The public status distinguishes prepared/enabled, not a claim that a recent inference succeeded; the actual attempt history supplies that evidence.
