# Legal Graph Contract (4A.1)

Schema version: `1.0.0`  
Act: Income-tax Act, 2025  
Scope: node/edge normalization and validation. This document does not prescribe a graph database.

## 1. Source inventory

| Source | Current contents | Graph use |
| --- | --- | --- |
| `canonicalize_legal_json/*section_can.json` | 535 files; 8,215 `legal_units`, 556 external-node occurrences, section `graph_edges` | Section units, external placeholders, hierarchy and citation links |
| `canonicalize_legal_json/chapter_hierarchy.json` | 23 Chapters, 38 divisions, 30 subdivisions; 136 hierarchy edges | Shared hierarchy nodes and links |
| `txt_to_json/schedule1.json` through `schedule16.json` | 16 structured Schedules | Schedule roots exist; paragraph/part/table units still need canonicalization |

Observed legal unit types: `section`, `subsection`, `clause`, `sub_clause`, `sub_sub_clause`, `item`, `sub_item`, `condition`, `operative_clause`, `where_block`, `exclusion_block`, `explanation_block`, `longline`, `longline_clause`, `table`, `table_row`, `table_cell_item`, and `sub_clauses`. The last type occurs twice and should be reviewed: its source is a plural type label for individual nodes. Preserve the IDs and source label; normalize `sub_clauses` to graph type `sub_clause` only after verifying both records.

Observed external node types: `external_act_placeholder`, `external_provision_placeholder`, `external_regulation_placeholder`. Observed edges: `parent`, `contains`, `references`, `references_external`, `references_external_act`, `references_schedule`. Chapter nodes use source `unit_type` values `chapter`, `chapter_division`, `chapter_subdivision`.

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
| `is_placeholder` | Required boolean. `true` only for un-ingested external targets or an explicitly declared unresolved target. |
| `source_file` | Relative input path(s) recording provenance; can be `null` when unavailable. Never part of node identity. |

Allowed `node_type` values initially: all observed legal and hierarchy types above, `schedule`, `schedule_part`, `schedule_paragraph`, `schedule_subparagraph`, `schedule_note`, plus the three observed external placeholder types. New Schedule types must be confirmed against canonical Schedule output before import. Unknown types fail validation; they are not silently changed to `item`.

Preserve additional source properties in a `source_properties` object when they are not in this contract (for example `columns`, `row_number`, `column_label`, range metadata). This prevents data loss while keeping the shared fields stable.

## 3. Hierarchy and source properties

All nodes expose nullable `chapter_id`, `chapter_number`, `chapter_title`, `division_id`, `division_number`, `division_title`, `subdivision_id`, `subdivision_number`, `subdivision_title`, `section_id`, and `section_number`. Map the canonical field `chapter` to `chapter_number`. Map the rest by name. Hierarchy nodes have `section_id = null` and `section_number = null`; Schedule nodes also have no section ID unless a future explicit mapping is defined. An absent division or subdivision uses `null` for every field in that group, including its title (the current source often uses an empty string).

All source-derived nodes expose nullable `page_start`, `page_end`, `line_start`, `x`, and `y_start`. Numeric coordinates stay numeric. Do not invent missing pages or coordinates. `source_file` identifies the section file, Chapter catalog, or Schedule file that supplied the record; repeated provenance can be a sorted list of paths.

For external placeholders, also expose nullable `target_act_title`, `target_act_year`, `target_act_number`, `target_jurisdiction`, `target_locator`, and required `resolution_status`. Map existing `act_title`, `act_year`, `act_number`, and `jurisdiction` to those target fields. For provisions, derive `target_locator` from the source `section`, `nested_locators`, and `locator_text` fields without dropping any of them. Preserve `official_citation`, `identity_basis`, `title_year`, and `source_mentions` in `source_properties`. External placeholder text is `null`; a name or locator is not the Act's legal text.

## 4. Edges

Every normalized edge has required nonempty `from_id`, `to_id`, `edge_type`; nullable `reference_text`, `source_file`, `resolution_status`, and `resolution_basis`. Allowed initial edge types: `parent`, `contains`, `references`, `references_external`, `references_external_act`, `references_schedule`. Retain source edge details such as `reference_kind`, range positions, target Act identity, Schedule locator, and human override notes in `source_properties`.

`parent` points from child to parent. `contains` points from parent to child. A reference points from the text making the citation to the cited target. For a reference with several range members, each target retains its own edge and range metadata. Incoming lookup uses an index on `to_id`, not another edge type.

Set `resolution_status = resolved` only when the target node is present and verified. An external placeholder with a valid node ID remains `placeholder`/`external_act_not_ingested`, as appropriate; it is not a verified legal provision. A missing internal target remains `unresolved` in a validation report or pending-edge collection and is not inserted as a resolved edge. Preserve its original `to_id`, source, and `reference_text` for later correction. `resolution_basis` is `null` unless a source supplies it (for example `human_override`).

## 5. Parent selection

Section parent: deepest existing Chapter subdivision; otherwise division; otherwise Chapter. Below a section, use the canonical `parent_id` as the authority: a subsection belongs to its section, a clause can belong to a section or subsection, a sub-clause to its owning clause or other explicit structural owner, a table to its owning provision, and a row to its table. A table-cell item belongs to its row or explicitly recorded cell owner. Do not derive a parent by splitting the ID: structures such as operative clauses and longlines have exceptions.

Schedule roots are independent roots. Once Schedule content is canonicalized, its parts, paragraphs, tables, rows, and notes attach to their explicit owners. An external Act placeholder is a root; an external provision placeholder may point to its named external Act placeholder. A Chapter root has `parent_id = null`.

## 6. Identity and duplicate rules

Preserve source IDs exactly. Graph loading must not generate or rewrite them. The graph key is the full ID string, including the `ext-...` namespace for external placeholders and `schedule-...` for Schedules. All imported records must use the same Act/version namespace if additional Acts or versions are introduced; do not silently collide with the current unprefixed section IDs.

If two occurrences share an ID, compare their normalized identity and legal content (`node_type`, citation, parent, text, Act identity, hierarchy and target locator). Merge when these agree, unioning and sorting `source_file` and `source_mentions`. Report a conflict when substantive fields disagree; never choose one arbitrarily. Ignore provenance order, empty/null formatting differences, and duplicate source mentions when comparing identity. For edges, deduplicate only when `from_id`, `to_id`, `edge_type`, normalized `reference_text`, resolution basis, and material locator/range attributes agree. Retain distinct reference occurrences or provenance when they do not.

## 7. Validation contract

Before storing a graph, check all of the following:

1. Required node and edge fields are present; `node_type` and `edge_type` are allowed.
2. Every graph ID is unique after approved merges; conflicting duplicates produce a blocking report.
3. Every structural parent exists. Each non-root node has exactly one structural parent; roots have none.
4. Every inserted edge source exists. Every resolved edge target exists. An unresolved target remains in the pending report.
5. Each `parent` edge agrees with the child's `parent_id` and has one inverse `contains` edge; neither relation creates a structural cycle.
6. Every section's Chapter/division/subdivision path matches `chapter_hierarchy.json` and its `parent_id` points to the deepest applicable hierarchy node.
7. Nullable hierarchy fields are consistent: absent division/subdivision groups are entirely `null`; hierarchy and Schedule roots have null section fields.
8. External placeholders retain their namespaced IDs and pending status; no placeholder is treated as ingested statutory text.
9. Schedule reference targets must be present as graph nodes before their edges can be marked resolved. Source Schedule JSON alone does not guarantee canonical paragraph or row targets.

## 8. Present input gaps

- Section 2 has a missing canonical table unit; Section 334 contains a Section 2 object in its raw file. Both are existing validation errors and must be corrected before a strict graph import.
- The 16 Schedule files are structured source JSON. They are not yet `legal_units` with graph edges. Schedule roots and referenced row/note IDs need an explicit canonicalization pass, with IDs matching existing `references_schedule` targets.
- External placeholders occur in multiple section files. Repeated `source_mentions` and file paths should be merged as provenance; inconsistent Act identity data must be reported.
- The two `sub_clauses` nodes need source review before normalizing that type to `sub_clause`.
