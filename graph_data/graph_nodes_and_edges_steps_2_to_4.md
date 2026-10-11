# Graph preparation: Steps 1-8

This explains the eight steps of the graph contract for the Income-tax Act, 2025, from source inventory through pre-import validation. The files described here are prepared graph data, **not** a loaded graph database or hybrid search index. Canonical IDs link graph nodes to any future search-index documents.

## Step 1: Source inventory

**Input:** `canonicalize_legal_json/*section_can.json`, `canonicalize_legal_json/chapter_hierarchy.json`, and `canonicalize_schedule_json/schedule*_can.json`.

**Work:** Identify the legal unit types, fields, hierarchy nodes, source references, Schedule structures, and external placeholders already present. This is an inventory, not a transformation. It establishes the allowed types and the source files that later collectors must read.

**Output:** The inventory and schema decisions in `docs/graph_schema_v1.md`. Current sources contain 535 canonical section files (8,214 active section units), 91 Chapter/Division/Subdivision nodes, and 16 canonical Schedule files (1,249 units). External-placeholder occurrences exist in the section JSON but are deferred from the active graph.

## Inputs and outputs

The inputs are:

- `canonicalize_legal_json/*section_can.json`: canonical sections, their hierarchy and references.
- `canonicalize_legal_json/chapter_hierarchy.json`: shared Chapter, Division, and Subdivision nodes and edges.
- `canonicalize_schedule_json/schedule*_can.json`: canonical Schedule units and edges.

The prepared outputs are in `graph_data/`:

| File | Meaning |
| --- | --- |
| `candidate_graph_nodes.jsonl` | One normalized record per source unit. These were collected before an identity-merge step. |
| `candidate_graph_nodes_summary.json` | Counts, node types, and mapping results. |
| `candidate_graph_nodes_report.csv` | Field coverage and mapping issues. |
| `resolved_graph_edges.jsonl` | Normalized edges whose source and target IDs each have one candidate node. |
| `pending_graph_edges.jsonl` | References that cannot yet be treated as verified edges. This audit file also retains external references, but they are **not part of the active graph**. |
| `graph_edges_summary.json` | Edge counts and reasons for pending records. |
| `graph_edges_report.csv` | One row per pending or invalid edge. |
| `graph_nodes.jsonl` | Step 6 active nodes after identity checks. |
| `graph_edges.jsonl` | Step 6 active edges after duplicate checks. |
| `graph_pending_edges.jsonl` | Step 6 pending edges; external targets remain here. |
| `graph_node_conflicts.jsonl`, `graph_identity_report.csv`, `graph_identity_summary.json` | Step 6 conflicts, merge report, and counts. |
| `graph_parent_validation_report.csv`, `graph_parent_validation_summary.json` | Step 5 parent and inverse-edge checks. |
| `graph_import_validation_report.csv`, `graph_import_validation_summary.json` | Step 7 graph-integrity and source-table validation. |

The collector programs are `collect_graph_nodes.py` and `collect_graph_edges.py`. They do not write to a graph database and do not create embeddings.

## Step 2: Universal node record

**Input:** `legal_units` from canonical sections and Schedules, plus `hierarchy_nodes` from the Chapter catalog.

**Output:** `graph_data/candidate_graph_nodes.jsonl`, `candidate_graph_nodes_summary.json`, and `candidate_graph_nodes_report.csv`. These are candidate records; no IDs are generated or changed.

### 2.1 Collect legal units

The node collector reads `legal_units` from 535 canonical section files, `hierarchy_nodes` from the Chapter catalog, and `legal_units` from 16 canonical Schedule files. It produces 9,554 candidate node records: 8,214 section units, 91 hierarchy units, and 1,249 Schedule units. The IDs are unique in the current candidate set; the earlier extra node `2` in Section 334 has been removed.

### 2.2 Map shared fields

Every candidate record uses the same top-level fields. `id`, `node_type`, and `citation` must be nonempty. The other shared fields can be `null` if the source does not have a value.

| Graph field | Source or rule |
| --- | --- |
| `id` | Preserve the canonical ID exactly. |
| `node_type` | Map the source `unit_type` without changing its legal meaning. |
| `citation` | Copy the canonical citation. |
| `parent_id` | Copy the recorded structural parent; a root has `null`. |
| `title`, `text`, `retrieval_text` | Copy the unit's own title/text/search text. An empty source value becomes `null`. |
| `act`, `jurisdiction` | Copy source metadata. |
| `is_placeholder` | `false` for every node currently collected. |
| `source_file` | Path to the canonical file supplying this occurrence. |
| `source_properties` | Other source fields, such as a table row's `columns` or `row_number`. |

For example, the record for `2-112` has `node_type = subsection`, `citation = Section 2(112)`, `parent_id = 2`, and `source_file = canonicalize_legal_json/2section_can.json`. A Schedule root such as `schedule-XV` keeps its existing ID and citation; its text can be `null` while its title and retrieval text remain available.

The collector reports **zero required-field mapping errors**. It has not generated embeddings or a search index.

### 2.3 External Acts at this stage

No external Act or external provision placeholder is included in `candidate_graph_nodes.jsonl`. The 556 external-node occurrences still exist in canonical source files and are counted as deferred. They are not active graph nodes. This matches the current decision to postpone external Acts.

## Step 3: Hierarchy and source properties

**Input:** The same canonical units and Chapter catalog used in Step 2, including their recorded `parent_id`, hierarchy fields, source-file paths, pages, and coordinates.

**Output:** The hierarchy and source fields on each record in `candidate_graph_nodes.jsonl`, with field-coverage and mapping findings in `candidate_graph_nodes_report.csv`. Step 3 does not create a separate node file.

### 3.1 Chapter path on section units

Section units carry `chapter_id`, `chapter_number`, and `chapter_title`. Where applicable, they also carry the Division and Subdivision `id`, `number`, and `title`. The canonical `chapter` value maps to graph `chapter_number`.

For example, Section 334 has:

```text
chapter-XVII
  -> chapter-XVII-division-B
     -> chapter-XVII-division-B-subdivision-2
        -> 334
```

Its graph record has `parent_id = chapter-XVII-division-B-subdivision-2`, `chapter_number = XVII`, `division_number = B`, and `subdivision_number = 2`. Section 334's clauses and other descendants retain their recorded `section_id` and `section_number`.

### 3.2 Nulls for non-section units

Chapter, Division, and Subdivision nodes have `section_id = null` and `section_number = null`. Schedule nodes also have null section fields. If a Division or Subdivision does not apply, its entire field group is `null`, including the title. The collector normalizes empty source strings to `null` for these shared fields.

### 3.3 Source position and provenance

Each node supports `page_start`, `page_end`, `line_start`, `x`, and `y_start`. Available coordinates stay numeric; unavailable coordinates remain `null`. `source_file` points to the canonical section file, canonical Schedule file, or Chapter catalog. No missing page or coordinate is guessed.

The collector checks the Chapter/section field presence, complete Division and Subdivision field groups, Schedule and hierarchy null section fields, numeric coordinates, and source-file provenance. The current summary reports **zero hierarchy or source-property issues** across 9,554 candidate nodes.

## Step 4: Edges

**Input:** `graph_edges` arrays from canonical section files, canonical Schedule files, and the Chapter catalog; `candidate_graph_nodes.jsonl` supplies the ID catalog used to classify endpoints.

**Output:** `graph_data/resolved_graph_edges.jsonl`, `pending_graph_edges.jsonl`, `graph_edges_summary.json`, and `graph_edges_report.csv`. They are classified edge occurrences, not yet the final stored graph.

### 4.1 Read the existing graph edges

The edge collector reads `graph_edges` from the same 535 section files, 16 Schedule files, and the Chapter catalog. It does not infer new legal references. It found 23,724 source edge occurrences.

Every normalized edge carries `from_id`, `to_id`, `edge_type`, `reference_text`, `source_file`, `resolution_status`, `resolution_basis`, and `source_properties`. The first three are required. A range member's position, Schedule locator, or human-review note stays in `source_properties`; the collector does not flatten several range targets into one edge.

### 4.2 Meaning of the edge types

| Edge type | Direction and meaning |
| --- | --- |
| `parent` | Child -> structural parent, such as `2-112 -> 2`. |
| `contains` | Structural parent -> child, the inverse of `parent`. |
| `references` | Citing legal unit -> cited internal legal unit. |
| `references_schedule` | Citing legal unit -> canonical Schedule unit or row. |
| `annotates` | Note/footnote -> unit it comments on. This is not a parent edge. |
| `references_external`, `references_external_act` | Recorded for audit as pending; excluded from the active graph for now. |

For example, `schedule-IV-footnote48 -> schedule-IV-t13A` is an `annotates` edge. A reference to `Schedule II (Table: Sl. No. 2)` points to the canonical row ID `schedule-II-t2` and keeps its Schedule locator metadata.

Incoming references will eventually be found by looking up edges whose `to_id` is the requested node. We have not built a database index on `to_id` yet; this is a requirement for the later storage step.

### 4.3 Decide which edges are verified

An edge goes into `resolved_graph_edges.jsonl` only if its source and target IDs each occur exactly once among candidate nodes and its type is allowed. The collector sets `resolution_status = resolved` for those records. The result is **23,161 resolved edge occurrences**. These are collected records, not yet deduplicated database edges.

The other **563 occurrences** are in `pending_graph_edges.jsonl`:

- 551 external Act/provision references are pending because external nodes have not been ingested. Their source Act identity and locator fields are retained for later work. They are not active edges.
- 12 references have a target ID absent from the candidate node catalog. Their `from_id`, `to_id`, `reference_text`, source file, and any override metadata remain available for review.

There are **zero invalid edge records**. The former `2-112 -> 2` ambiguity disappeared after rebuilding Section 334; that parent edge is now resolved. Pending does not mean a link can be guessed from similar wording. No missing target is inserted as a resolved edge.

## Step 5: Parent selection and structural links

**Input:** `candidate_graph_nodes.jsonl`, `resolved_graph_edges.jsonl`, and the `parent_id` and Chapter path already recorded on each node. `validate_graph_parents.py` checks these inputs; it does not derive parents by splitting IDs.

**Rule:** A section attaches to its deepest applicable Chapter subdivision, otherwise Division, otherwise Chapter. Below a section, the canonical `parent_id` is authoritative. A Schedule root and Chapter root have no parent; Schedule descendants use their explicit owners. Each non-root node must have one `child -> parent` edge and exactly one inverse `parent -> child` `contains` edge. `annotates` does not change parentage.

**Output:** `graph_parent_validation_report.csv` and `graph_parent_validation_summary.json`. The current check found 9,515 parent links and 9,515 matching `contains` links across 9,554 nodes, with zero parent errors. This step validates structure; it does not modify the node or edge files.

## Step 6: Identity and duplicate rules

**Input:** `candidate_graph_nodes.jsonl`, `resolved_graph_edges.jsonl`, and `pending_graph_edges.jsonl`. The program is `graph_data/deduplicate_graph_records.py`.

**Rule:** The entire source ID is the graph key. Matching occurrences with the same ID can merge only when normalized legal identity and content agree; provenance is unioned and sorted. Conflicting IDs are reported, not arbitrarily chosen. Edges merge only when endpoints, type, normalized reference text, resolution basis, and material range/locator attributes agree. Distinct occurrences or provenance remain traceable. A resolved edge touching a conflicting node moves to pending.

**Output:** `graph_nodes.jsonl`, `graph_edges.jsonl`, `graph_pending_edges.jsonl`, `graph_node_conflicts.jsonl`, `graph_identity_report.csv`, and `graph_identity_summary.json`. Current result: 9,554 active nodes, 23,161 active edges, 563 pending edges, zero duplicate merges, and zero conflicting IDs. The empty conflict file and header-only report are expected for this run.

## Step 7: Pre-import validation contract

**Input:** Step 6 active/pending files and conflict file; `canonicalize_legal_json/chapter_hierarchy.json`; canonical Schedule JSON; raw and canonical section JSON for the source-table check. The program is `graph_data/validate_graph_import.py` and it uses `graph_data/validate_graph_parents.py`.

**Checks:** Required fields and allowed types; unique active IDs; root and parent rules; edge endpoints and resolution status; exact inverse `parent`/`contains` links and no cycles; section placement against Chapter catalog ranges; complete nullable hierarchy groups; external targets remaining namespaced and pending; Schedule targets matching canonical Schedule IDs; and `annotates` endpoints. A direct raw-versus-canonical table-count check detects tables lost during canonicalization. It does not establish that every legal word or cross-reference has been audited.

**Output:** `graph_import_validation_report.csv` and `graph_import_validation_summary.json`. The current run checked 9,554 nodes, 23,161 active edges, and 563 pending edges. Graph integrity has **zero errors**. Strict import is **not ready** because the source-table check found one missing canonical table; two type-review warnings remain. A nonzero validator exit code is expected while that blocking error exists.

## Step 8: Present input gaps and decision

**Input:** The Step 7 validation files, the current raw/canonical Section 2 files, the Step 6 conflict report, and pending-reference collection.

**Output:** A tracked decision about what must be corrected before strict import, not another graph data file:

| Finding | Evidence | Action/status |
| --- | --- | --- |
| Section 2 canonical table is missing | Raw Section 2 has one table; canonical Section 2 has no `table` unit. `graph_import_validation_report.csv` marks this `ERROR`. | Correct canonicalization/source mapping, rebuild downstream nodes and edges, then rerun Steps 5-7. Strict import remains blocked. |
| Section 334 stray Section 2 object | Current Section 334 canonical file was rebuilt and current node IDs are unique. | Fixed. The older `canonical_evaluation_report.csv` predates this change and is not current evidence. |
| External Act references | 551 external reference occurrences remain pending with namespaced target IDs and metadata; external nodes are not ingested. | Intentionally deferred. Do not treat placeholders as statutory text. |
| Missing internal targets | 12 pending reference occurrences have no current target node. | Review/correct separately; none is promoted to a resolved edge. |
| `sub_clauses` source type | Two nodes (`416-5-g-a`, `416-5-g-b`) retain this plural source label. | Review source meaning before renaming; warnings do not silently coerce the type. |

No graph database, incoming-edge index, BM25 index, vector index, embeddings, or retrieval service has been built in these eight steps. Source-content verification beyond the table-count check is still required for a production legal system.
