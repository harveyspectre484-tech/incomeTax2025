# Income-tax Act, 2025 Legal RAG Roadmap

# **What Each File Says**

[plan.md](/Users/avikalchauhan/incometax2025anti/incomeTax2025/plan.md) is the **general architecture recommendation** for a legal AI system. It explains why legal RAG needs more than vector search and recommends combining hybrid search, a legal graph, structure-aware units, metadata, reranking, and an LLM. It is broad guidance, not a checklist of work completed in your project.

[project_plan.md](/Users/avikalchauhan/incometax2025anti/incomeTax2025/project_plan.md) applies that general idea to **your Income-tax Act, 2025 project**. It turns the architecture into numbered steps and records project-specific status, such as canonical units, graph nodes and edges, table indexing, BM25, vector search, retrieval, and evaluation.

**How They Relate**

`plan.md` describes the destination; `project_plan.md` is the project roadmap for getting there. The second is more specific and should guide your next actions. The first is background rationale.

There is one important mismatch: `project_plan.md` says the vector index is not complete, but you’ve told me you have stored embeddings in a vector database. Its status is therefore stale and should be updated. The plan also treats table context as part of preparing search documents, while table quality still needs checking even if the indexes exist.

In short: **use `project_plan.md` to track the work, and use `plan.md` to understand the overall design.**

## Target Pipeline

```text
Structured JSON
    -> Canonical legal units
    -> Legal graph + hybrid search indexes
    -> Retrieve candidates
    -> Expand legal dependencies
    -> Rerank evidence
    -> LLM answer with citations
    -> Evaluate and maintain legal versions
```

The graph, BM25 index, and vector index share the same canonical unit IDs. External Acts are currently deferred. When a question depends on an external provision whose text is not in the corpus, retrieval must disclose that limitation instead of treating a placeholder as legal text.

## Step 1: Structure Source Law as JSON

**Purpose:** Preserve the source law's legal organization and wording.

Represent sections, subsections, clauses, nested clauses, tables, rows, cell items, Schedules, notes, footnotes, and source locations. Keep source extraction data separate from retrieval logic.

**Status:** Structured section and Schedule JSON exists. Continue correcting extraction or hierarchy defects in the raw source when validation identifies them.

## Step 2: Create Canonical Legal Units

**Purpose:** Give every independently meaningful provision a stable ID and its own exact text.

Canonicalize the units while preserving source IDs, legal parentage, citation, source position, and compact retrieval text. Keep table rows and meaningful table-cell items identifiable as their own units.

**Status:** Section and Schedule canonicalizers and validators exist. Known issue: Section 2 has one raw table that is missing from its canonical units. Two source units use the plural type `sub_clauses` and need review before normalization.

## Step 3: Create Hierarchy and Reference Relationships

**Purpose:** Record where each unit sits and which provisions it cites.

Build Chapter, Division, and Subdivision nodes from `chapter.md`; connect sections to their deepest applicable hierarchy parent. Extract internal section, Schedule, range, and other explicit references. Keep external-Act references pending because external Act text is not currently ingested.

**Status:** Chapter hierarchy and explicit reference edges are represented in canonical outputs. Unresolved internal references remain reportable pending items; do not invent targets.

## Step 4A: Build the Legal Graph

### 4A.1 Define the graph schema

Define allowed node and edge types, common fields, nullable Chapter/Division/Subdivision metadata, ID rules, provenance, duplicate handling, and validation rules.

**Status:** Defined in [graph_schema_v1.md](graph_schema_v1.md), schema version `1.2.0`.

### 4A.2 Collect candidate graph nodes

Collect section `legal_units`, Chapter hierarchy nodes, and Schedule `legal_units` into candidate records. Preserve canonical IDs and source-file provenance. External placeholders are counted as deferred and excluded from active nodes.

**Status:** Collector and candidate collection exist. Latest summary reports 9,558 candidate records, 9,558 unique IDs, 8,214 section units, 91 hierarchy nodes, and 1,249 Schedule units. No candidate IDs were merged.

### 4A.3 Check node identities

Compare records sharing an ID. Merge only records whose legal identity and content agree; report conflicting duplicates. Preserve provenance from matching occurrences.

**Status:** Completed for the current candidate set. Latest identity summary reports zero conflicting IDs and zero duplicate merges.

### 4A.4 Collect graph edges

Collect structural, internal reference, Schedule reference, and annotation edges. Preserve edge provenance and range/locator metadata. Keep external-reference edges in the pending audit collection.

**Status:** Edge collection and identity handling exist. Latest summaries report 23,161 active/resolved edge occurrences and 563 pending edge occurrences. The pending set includes external references and missing internal targets.

### 4A.5 Validate graph connections

Check required fields, endpoints, unique parentage, inverse parent/contains relationships, cycles, Chapter paths, Schedule references, annotations, and source table completeness.

**Status:** Graph integrity checks pass. Strict graph import is not ready: the latest report has one blocking missing canonical table in Section 2 and two warnings for `sub_clauses` node types. Fix those source/canonicalization issues and rebuild downstream graph files before strict import.

### 4A.6 Store and index the graph

Load approved nodes, resolved edges, and pending edges into persistent graph storage. Index node IDs and both `from_id` and `to_id` so parent/child and incoming/outgoing-reference lookups are fast. SQLite tables are sufficient at the current scale; a graph database is optional.

**Status:** Prepared JSONL graph files exist. A persistent graph store with lookup indexes has not yet been built.

### 4A.7 Test graph lookups

Test parent, children, outgoing references, incoming references, Schedule references, and Chapter paths using known provisions. Verify results by canonical ID and citation.

**Status:** Not complete. Run after graph storage is built.

## Step 4B: Build the Hybrid Search Index

### 4B.1 Prepare index documents

Create one search document per substantive unit. Include exact text, citation, title, hierarchy metadata, and table context such as headers and row number. Preserve the canonical ID used by the graph.

**Status:** Index document builder exists. Latest summary reports 8,865 documents from 9,558 input nodes; structural containers and empty or very short units are skipped by explicit rules.

### 4B.2 Build BM25 search

Index exact citations, section numbers, legal wording, titles, and retrieval text. Weight citations and section identifiers strongly. Confirm exact citation queries work.

**Status:** SQLite FTS5 BM25 index exists at `graph_data/legal_act_bm25.sqlite`. It can be rebuilt with `tools/build_hybrid_index.py`.

### 4B.3 Build vector search

Choose an embedding model, embed compact retrieval text for the same index documents, and store vectors with canonical IDs and metadata filters. Do not embed entire Acts or large duplicated parent contexts.

**Status:** Not complete. Select and document the embedding model and vector store before generating embeddings.

### 4B.4 Combine hybrid results

Search BM25 and vectors independently, then combine ranked results with Reciprocal Rank Fusion or another evaluated rank-fusion method. Keep exact citation boosts and metadata filters available.

**Status:** Not complete. BM25 currently exists without a vector result set to fuse.

## Step 5: Retrieve Candidate Provisions

Accept a user query and return a ranked, compact set of candidate units from hybrid search. Preserve each unit's ID, citation, text, score, and retrieval reason. Support both exact legal citations and conceptual questions.

Example tests:

- Exact: `Section 36(3)(a)(i)`
- Conceptual: `Which business expenses are not deductible?`
- Table-oriented: `What deduction limit applies to this category?`

**Status:** BM25 search exists as a component. Hybrid retrieval and a tested query interface remain to be built.

## Step 6: Expand Legal Dependencies

Use the graph to add only context that the retrieved provision needs:

- structural parents, introductions, conditions, and exceptions;
- outgoing references to provisions it cites;
- incoming references from provisions that cite it;
- relevant table headers and rows;
- applicable Schedule paragraphs and rows;
- definitions and applicability provisions when identified.

Deduplicate evidence and retain the IDs and citations. For non-explicit relationships such as `defines`, `restricts`, or `creates_exception_to`, require supporting text, scope, confidence, and review status before adding an edge. Use targeted follow-up search when conditions or limits appear to be missing.

**Status:** The source graph contains explicit relationships. Query-time dependency expansion, definition/applicability lookup, and targeted follow-up are not complete.

## Step 7: Rerank Evidence

Rerank the retrieved and expanded candidates with a cross-encoder or other evaluated reranker. Keep the strongest legally relevant evidence and remove merely similar passages. Preserve exact citation matches and required parent context.

**Status:** Not complete. Select a reranker and evaluate it against manually judged legal query examples.

## Step 8: Generate Answers with the LLM

Send only the final compact evidence set to the LLM. Require it to:

- answer from retrieved corpus evidence;
- cite sections, subsections, clauses, and table rows precisely;
- distinguish statutory text from explanation;
- say when the corpus lacks a needed provision, including deferred external Acts;
- avoid inventing legal rules or silently resolving ambiguity.

**Status:** Not complete. Build this only after retrieval and expansion return dependable evidence.

## Step 9: Evaluate and Maintain the Corpus

Create a test set covering exact citations, conceptual questions, nested clauses, tables, Schedule references, ranges, incoming references, exceptions, and insufficient-context cases. Measure retrieval recall, citation correctness, answer support, and abstention. Track the Act version and amendment/effective dates so answers can be tied to the correct legal text.

**Status:** Ongoing requirement. Production readiness requires evaluation and a plan for amendments and versioned law.

## Immediate Next Actions

1. Correct Section 2's missing canonical table and review the two `sub_clauses` types.
2. Rebuild candidate nodes and edges, then rerun graph validation.
3. Build persistent graph storage with indexed incoming and outgoing lookups.
4. Test graph queries against known section and Schedule references.
5. Select an embedding model and vector store; build the vector index.
6. Combine BM25 and vector retrieval, then implement dependency expansion and reranking.
7. Add evidence-grounded LLM answering and an evaluation set.
