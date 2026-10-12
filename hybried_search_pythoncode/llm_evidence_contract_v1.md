# Pre-LLM evidence contract v1

`live_legal_retrieve.py` selects bounded units from reranked expansion bundles
as `final_evidence.evidence_units` before calling the answer model. Do not pass
`retrieval_text`, unrelated graph nodes, or Qdrant payload text as legal authority.

Each evidence unit has a canonical `id`, `citation`, `unit_type`, exact `text`,
`parent_id`, `act`, `version`, and `jurisdiction`. `version` is the corpus's
Income-tax Act title year (2025), **not** a verified effective date. Preserve
`source_file` and `source_year` as provenance when present. Schedule III row
38A and its notes have 2026 source-year metadata and amendment footnotes;
the answer stage must check the applicable date before answering. A table
container may have empty `text`; its `table_columns` and `display_text` carry
the source column labels. Table rows carry their exact row text and retain
their table as `parent_id`. `display_text` is citation plus the exact text or
table labels for reading; it is not a separate legal source.

`inclusion_reason`, `relationship_to_query`, and `origin_id` record why each
unit was included and which ranked anchor supplied it. The selector deduplicates
canonical IDs, retains governing parents and table headers when they fit, and
records omissions. The default limits are `--max-bundles 10`,
`--max-score-drop 1.0`, and `--max-context-chars 16000`. Exact-citation anchors
remain eligible despite the score threshold. Reranker scores are ranking
signals, not calibrated legal-confidence probabilities. Review
`omitted_bundles` and `omitted_units` for missing governing text.

The five-case `--test` report measures required-unit recall and a
`focus_precision_proxy` against explicitly listed focus IDs. Its focus list
is a small engineering review set, **not** a legal gold standard. Passing it
does not certify an answer or establish that all unrelated units were removed.
The live chat passes these units to one structured LLM generation call. It does
not run the post-generation citation, quote, or quantity validator or a repair
pass. The model can still abstain, but its claims are not mechanically verified;
users must check cited provisions before relying on an answer.
The bank percentage case can still include Section 31(2) material inside a
retained expansion bundle; the LLM must not assume it applies.
