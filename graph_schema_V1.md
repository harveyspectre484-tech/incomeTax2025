# Legal Graph Contract (4A.1)

Schema version: `1.2.0`  
Act: Income-tax Act, 2025  
Scope: node/edge normalization and validation. This document does not prescribe a graph database.

## 1. Source inventory

| Source | Current contents | Graph use |
| --- | --- | --- |
| `canonicalize_legal_json/*section_can.json` | 535 files; 8,215 `legal_units`, 556 external-node occurrences, section `graph_edges` | Section units and internal hierarchy/citation links; external placeholders are deferred and excluded |
| `canonicalize_legal_json/chapter_hierarchy.json` | 23 Chapters, 38 divisions, 30 subdivisions; 136 hierarchy edges | Shared hierarchy nodes and links |
| `canonicalize_schedule_json/schedule1_can.json` through `schedule16_can.json` | 16 files; 1,249 `legal_units` and Schedule `graph_edges` | Schedule roots, parts, paragraphs, tables, rows, notes, footnotes and their links |

Observed legal unit types: `section`, `subsection`, `clause`, `sub_clause`, `sub_sub_clause`, `item`, `sub_item`, `condition`, `operative_clause`, `where_block`, `exclusion_block`, `explanation_block`, `exception_block`, `longline`, `longline_clause`, `table`, `table_row`, `table_cell_item`, and `sub_clauses`. The last type occurs twice and should be reviewed: its source is a plural type label for individual nodes. Preserve the IDs and source label; normalize `sub_clauses` to graph type `sub_clause` only after verifying both records.

Observed Schedule unit types: `schedule`, `part`, `paragraph`, `subparagraph`, `clause`, `sub_clause`, `sub_sub_clause`, `sub_sub_sub_clause`, `item`, `table`, `table_row`, `table_cell_item`, `note`, `footnote`, `formula`, `variable`, and `substitution`. External source placeholders are inventoried but are outside the active graph scope. Active observed edges are `parent`, `contains`, `references`, `references_schedule`, and `annotates`. Chapter nodes use source `unit_type` values `chapter`, `chapter_division`, `chapter_subdivision`.

## 2. Universal node record

Every normalized node has these keys. `id`, `node_type`, and `citation` must be nonempty strings. All other keys are present but may be `null` unless stated otherwise.

| Property | Meaning and mapping |
| --- | --- |
| `id` | Exact canonical ID; unique across the entire graph. |
| `node_type` | `legal_units[].unit_type`, hierarchy `unit_type`, or external `node_type`, subject to the allowed types below. |
| `citation` | Existing citation, or a deterministic display citation assembled from existing external metadata; no new ID is generated. |
| `parent_id` | Existing structural parent ID, or `null` for a root. |
| `title` | Existing title; external placeholders use the target Act title as a display title. |
| `text` | Exact text of this unit, or `null` when the source has no ingested provision text. |
| `retrieval_text` | Existing compact search text, or `null` for an external placeholder without ingested text. |
| `act` | The source Act for ingested legal units; external nodes use their named target Act when known. |
| `jurisdiction` | Existing jurisdiction; do not infer a missing value from an ID. |
| `is_placeholder` | Required boolean. Always `false` in the current active graph because external placeholders are deferred. |
| `source_file` | Relative input path(s) recording provenance; can be `null` when unavailable. Never part of node identity. |

Allowed `node_type` values initially: all observed section, Schedule, and hierarchy types listed above. External placeholder types are not active node types. Use the canonical Schedule names (`part`, `paragraph`, `note`, etc.) without adding a `schedule_` prefix. Unknown types fail validation; they are not silently changed to `item`.

Preserve additional source properties in a `source_properties` object when they are not in this contract (for example `columns`, `row_number`, `column_label`, range metadata). This prevents data loss while keeping the shared fields stable.

## 3. Hierarchy and source properties

All nodes expose nullable `chapter_id`, `chapter_number`, `chapter_title`, `division_id`, `division_number`, `division_title`, `subdivision_id`, `subdivision_number`, `subdivision_title`, `section_id`, and `section_number`. Map the canonical field `chapter` to `chapter_number`. Map the rest by name. Hierarchy nodes have `section_id = null` and `section_number = null`; Schedule nodes also have no section ID unless a future explicit mapping is defined. An absent division or subdivision uses `null` for every field in that group, including its title (the current source often uses an empty string).

All source-derived nodes expose nullable `page_start`, `page_end`, `line_start`, `x`, and `y_start`. Numeric coordinates stay numeric. Do not invent missing pages or coordinates. `source_file` identifies the section file, Chapter catalog, or Schedule file that supplied the record; repeated provenance can be a sorted list of paths.

External placeholders are not mapped into the current candidate node collection. Their source records remain in canonical JSON and are counted as deferred so they can be introduced under a later schema version without losing provenance.

## 4. Edges

Every normalized edge has required nonempty `from_id`, `to_id`, `edge_type`; nullable `reference_text`, `source_file`, `resolution_status`, and `resolution_basis`. Allowed edge types: `parent`, `contains`, `references`, `references_external`, `references_external_act`, `references_schedule`, `annotates`. External-reference edges are collected as pending while external placeholder nodes remain deferred. Retain source edge details such as `reference_kind`, range positions, target Act identity, Schedule locator, and human override notes in `source_properties`.

`parent` points from child to parent. `contains` points from parent to child. A reference points from the text making the citation to the cited target. `annotates` points from a note or footnote to the unit it annotates; it does not change structural parentage. For a reference with several range members, each target retains its own edge and range metadata. Incoming lookup uses an index on `to_id`, not another edge type.

Set `resolution_status = resolved` only when both endpoints are present and unambiguous in the candidate node catalog. External references retain their source status, such as `external_act_not_ingested`, in the pending collection; an external placeholder ID is not verified legal text. A missing or ambiguous internal target remains `unresolved` or `ambiguous` in the pending-edge collection and is not inserted as a resolved edge. Preserve its original `to_id`, source, and `reference_text` for later correction. `resolution_basis` is `null` unless a source supplies it (for example `human_override`).

## 5. Parent selection

Section parent: deepest existing Chapter subdivision; otherwise division; otherwise Chapter. Below a section, use the canonical `parent_id` as the authority: a subsection belongs to its section, a clause can belong to a section or subsection, a sub-clause to its owning clause or other explicit structural owner, a table to its owning provision, and a row to its table. A table-cell item belongs to its row or explicitly recorded cell owner. Do not derive a parent by splitting the ID: structures such as operative clauses and longlines have exceptions.

Schedule roots are independent roots. Their canonical parts, paragraphs, tables, rows, and notes attach to their explicit owners. A Chapter root has `parent_id = null`.

## 6. Identity and duplicate rules

Preserve source IDs exactly. Graph loading must not generate or rewrite them. The graph key is the full ID string, including the `ext-...` namespace for external placeholders and `schedule-...` for Schedules. All imported records must use the same Act/version namespace if additional Acts or versions are introduced; do not silently collide with the current unprefixed section IDs.

If two occurrences share an ID, compare their normalized identity and legal content (`node_type`, citation, parent, text, Act identity, hierarchy and target locator). Merge when these agree, unioning and sorting `source_file` and `source_mentions`. Report a conflict when substantive fields disagree; never choose one arbitrarily. Ignore provenance order, empty/null formatting differences, and duplicate source mentions when comparing identity. For edges, deduplicate only when `from_id`, `to_id`, `edge_type`, normalized `reference_text`, resolution basis, and material locator/range attributes agree. Retain distinct reference occurrences or provenance when they do not.

The Step 6 pass is `tools/deduplicate_graph_records.py`. It reads the candidate nodes and the resolved/pending edge collections without changing their source files. Outputs are `graph_nodes.jsonl`, `graph_edges.jsonl`, `graph_pending_edges.jsonl`, `graph_node_conflicts.jsonl`, `graph_identity_report.csv`, and `graph_identity_summary.json`. Matching duplicate occurrences keep the same ID and aggregate sorted `source_files`, deduplicated `source_mentions`, and `occurrence_count`. A conflicting ID is excluded from active nodes, edges touching it move to pending as ambiguous, and the command exits unsuccessfully after writing the conflict report. External edges remain pending; this pass does not ingest external Act nodes.

## 7. Validation contract

Before storing a graph, check all of the following:

1. Required node and edge fields are present; `node_type` and `edge_type` are allowed.
2. Every graph ID is unique after approved merges; conflicting duplicates produce a blocking report.
3. Every structural parent exists. Each non-root node has exactly one structural parent; roots have none.
4. Every inserted edge source exists. Every resolved edge target exists. An unresolved target remains in the pending report.
5. Each `parent` edge agrees with the child's `parent_id` and has one inverse `contains` edge; neither relation creates a structural cycle.
6. Every section's Chapter/division/subdivision path matches `chapter_hierarchy.json` and its `parent_id` points to the deepest applicable hierarchy node.
7. Nullable hierarchy fields are consistent: absent division/subdivision groups are entirely `null`; hierarchy and Schedule roots have null section fields.
8. External placeholders are excluded from the active graph; external-reference edges are preserved in the pending collection with their target metadata and counted as deferred.
9. Schedule reference targets must match IDs in the canonical Schedule graph before their edges can be marked resolved; validate `annotates` endpoints independently of the parent hierarchy.

Run `tools/validate_graph_import.py` on the Step 6 `graph_data` files with `--hierarchy-file`, `--schedule-dir`, `--raw-dir`, and `--canonical-dir`. It writes `graph_import_validation_report.csv` and `graph_import_validation_summary.json`. The validator checks structural and reference integrity against the Chapter and Schedule catalogs, and compares raw versus canonical table counts as a source-completeness gate. `graph_integrity_ready` can be true while `strict_graph_import_ready` is false. A nonzero exit code means at least one blocking error; pending external references alone do not block the active graph. This table check is not a complete audit of all source content.

## 8. Present input gaps

- Section 2 still has a missing canonical table unit: one raw table versus zero canonical table units. The stray Section 2 object in the Section 334 raw file has been removed; the current candidate node set has no duplicate IDs. The older `canonical_evaluation_report.csv` predates this correction and should not be used as current evidence for Section 334.
- External placeholders occur in multiple section files. Repeated `source_mentions` and file paths should be merged as provenance; inconsistent Act identity data must be reported.
- The two `sub_clauses` nodes need source review before normalizing that type to `sub_clause`.
