# Scoped Dependency Discovery, First Pass

This is a source-reviewed engineering benchmark, not a legal opinion or an independent verification of the Income-tax Act text. The examples are deliberately small and were used while designing the first-pass rules; the scores are not an out-of-sample accuracy estimate.

## Inputs and outputs

- Inputs: existing `graph_nodes.jsonl` and `graph_edges.jsonl`, plus `discovery_cases_v1.json`.
- Each case supplies a question, a known starting canonical ID, expected evidence IDs, and a review note. It does **not** test whether BM25/Qdrant retrieves that starting ID.
- The evaluator first runs the existing parent/table/reference/annotation expansion, then proposes additional dependencies.
- Outputs: `dependency_discovery_v1.json` (per-case detail) and `dependency_discovery_v1.csv` (compact misses and additions).

## Rules in this pass

1. Include siblings of a retrieved condition when a small common owner introduces a `where` condition list.
2. For an explicit reference to an empty Schedule paragraph, inspect a bounded set of its operative descendants. Prefer descendants whose text overlaps the question.
3. If a retrieved provision ends in a dash introducing alternatives, select a child only when its text uniquely matches the question.
4. Extract quoted definitions with an explicit `in this paragraph`, `in this section`, `in this Schedule`, or `in this Chapter` scope. Propose the definition only when the term is used within that scope. Include a short continuation list when the definition ends in a dash.

Every added record remains a **candidate** with its source ID and scope. The script does not write inferred graph edges or assert that a candidate legally overrides another provision. It caps new units at six per anchor and does not perform open-ended traversal.

## Reviewed cases

The nine examples cover Section 31 domestic and foreign bank rows, Section 31 provision-account conditions, deemed write-off, Schedule III row 38A and its footnote, Schedule XV HUF premium limits, the local `actual capital sum assured` definition, a disability-policy alternative, and a no-unrelated-siblings check.

| Metric | Count |
| --- | ---: |
| Cases | 9 |
| Required evidence IDs absent from existing expansion | 17 |
| Required IDs still absent after discovery | 0 |
| Newly proposed IDs outside the reviewed expected sets | 0 |
| Cases with all expected IDs | 9 |

An "extra" here means a **newly proposed** ID outside a case's reviewed expected set; it does not judge every node already present in the baseline expansion. The expected sets were reviewed against the local canonical text, not by an independent legal expert. These rules and cases were developed together, so a separate holdout set is needed before claiming general retrieval quality.

## Limits and next checks

- Act-wide definitions without a reliable scope are not automatically attached.
- A candidate condition or exception is not a verified legal relationship. Applicability, tax year, and effective date still need review.
- No cross-bundle evidence deduplication or final LLM context selection happens here.
- Run a larger holdout set with independently reviewed evidence IDs, especially negative cases and cross-section exceptions. Then integrate only the supported discoveries into the live retrieval path.
