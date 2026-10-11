Step 4 converts your canonical JSON into **two separate but connected stores**:

```text
Canonical ID = connection between Graph and Search Index
```

For example, `36-3-a-i` must have the same ID everywhere.

# 4A. Build The Legal Graph

## 1. Create Graph Nodes

Import these as nodes:

- Chapter
- Division
- Subdivision
- Section
- Subsection
- Clause
- Sub-clause
- Item
- Table
- Table row
- Table-cell item
- Schedule
- External Act placeholder

Each node should contain only identifying metadata:

```json
{
  "id": "36-3-a-i",
  "unit_type": "sub_clause",
  "citation": "Section 36(3)(a)(i)",
  "section_number": "36",
  "title": "...",
  "chapter": "IV"
}
```

Do not store embeddings inside the graph initially.

## 2. Import Existing Edges

Load your canonical `graph_edges`:

```text
parent
contains
references
references_external
references_external_act
references_schedule
```

Example:

```text
36-3-a-i-table → parent → 36-3-a-i
36-3-a-i → parent → 36-3-a
123 → references_schedule → schedule-XV
```

## 3. Support Incoming And Outgoing Lookup

Your graph must efficiently answer:

```text
What does this unit reference?
Which units reference this unit?
What is its parent?
What children does it contain?
```

You do not need duplicate reverse reference edges. Create database indexes on both:

```text
from_id
to_id
edge_type
```

## 4. Keep Inferred Dependencies Separate

Later relationships such as:

```text
defines
restricts
creates_exception_to
caps
applies_to
```

should not be mixed with explicit statutory references unless they contain:

- supporting text
- source unit
- confidence
- review status
- scope

Start Step 4 using only your existing explicit edges.

## 5. Validate The Graph

Check:

- every node ID is unique
- every edge source exists
- internal edge targets exist
- every non-Chapter unit has a valid parent
- no parent cycles exist
- Section-to-Chapter paths are valid
- incoming and outgoing lookups return the correct units

---

# 4B. Build The Hybrid Search Index

## 1. Create Index Documents

Create one search document per substantive canonical unit.

Index:

- standalone sections
- subsections with their own operative text
- clauses
- sub-clauses
- items
- where/exclusion/explanation blocks
- table rows
- meaningful table-cell items
- Schedule paragraphs and rows

Do not index empty structural containers.

## 2. Prepare Each Search Record

Use a structure such as:

```json
{
  "id": "36-3-a-i",
  "citation": "Section 36(3)(a)(i)",
  "unit_type": "sub_clause",
  "title": "...",
  "text": "Exact statutory text",
  "retrieval_text": "Compact text used for searching",
  "parent_id": "36-3-a",
  "section_number": "36",
  "chapter": "IV",
  "act": "Income-tax Act, 2025",
  "jurisdiction": "India"
}
```

The `id` must match the graph node ID.

## 3. Build BM25 Index

BM25 should search these fields with different importance:

1. `citation` and `section_number`: highest
2. `title` and defined terms: high
3. `text`: normal
4. `retrieval_text`: normal
5. Chapter and unit type: metadata/filter fields

BM25 handles:

- exact citations
- exact statutory phrases
- percentages
- dates
- defined terms
- Act names

## 4. Build Vector Index

Generate an embedding for `retrieval_text`, not the entire section.

For a clause, embedding input can be:

```text
Section 36(3)(a)(i)
[section title]
[chapter/division title]
[exact clause text]
```

Keep the embedding input compact. Do not include every sibling or the complete section.

Store metadata with each vector so retrieval can filter by:

- Act
- jurisdiction
- Chapter
- section
- unit type
- tax year or effective date later

## 5. Handle Tables Carefully

Each table row should be independently searchable.

Its retrieval text should contain:

```text
parent citation
table heading
column headers
row number
row values
```

A row without its column headers is usually meaningless.
#### **Explanation of step 5**
1. Check the canonical data first. For every table, confirm each row has its own canonical unit with a stable ID, its owning table or provision as parent_id, and the row’s cell values. If rows are only nested inside a table object and aren’t canonical units, your indexing pipeline needs to emit separate row documents. Don’t invent IDs during indexing; use the existing canonical IDs. **made audit_canonical_tables.py**

2. Create one search record per row. Include the row’s canonical ID, citation, parent ID, exact row text, and metadata. Make retrieval_text self-contained by including:
- Parent provision citation and relevant title
- Table heading
- Column headers
- Row number and cell values
For example, a row from Section 31 might have retrieval text like:
Section 31(1), Table: Deduction for bad and doubtful debts. Columns: Sl. No.; Specified assessee; Amount of deduction. Row 1: Specified assessee: [row 1, column B text]. Amount of deduction: [row 1, column C text].
If a cell contains nested lettered items, preserve their labels and text in that cell’s value.

3. Verify IDs across stores. Each table-row search record’s ID should exist in both graph_nodes.jsonl and the canonical data. Check that every intended row appears in search_documents.jsonl exactly once.


4. Rebuild downstream indexes if you change records. Regenerate BM25 and re-embed the updated retrieval_text into Qdrant. Your embedding script upserts matching IDs, but removed or obsolete IDs can remain in Qdrant; use a fresh collection for a clean rebuild, or explicitly reconcile stale points.



## 6. Canonicalize Schedule Contents

Your section-to-Schedule references already resolve, but Schedule paragraphs and rows must also become canonical searchable units if you want paragraph-level Schedule retrieval.

Otherwise, graph expansion can locate `schedule-XV`, but search cannot retrieve a precise Schedule XV paragraph or table row.

#### **Explanation**
 **Current Status**

Step 6 is already mostly complete:

- All 16 Schedules have canonical files.
- Canonical Schedule units and graph nodes match.
- `164` Schedule table rows are searchable.
- Schedule table rows passed the table verification.
- `177` `references_schedule` edges are resolved.
- `0` Schedule-reference edges are pending.
- `43` Schedule `annotates` edges exist.

Do not canonicalize all Schedules again. Complete the following audit and indexing work.

**1. Audit Canonical Schedule Units**

For every Schedule, verify this hierarchy:

Every unit needs:

```text
id
citation
unit_type
parent_id
schedule_id
schedule_number
schedule_title
text
retrieval_text
act
jurisdiction
```

Empty structural containers are acceptable when their children contain the law. They should remain graph nodes but should not become search documents.

**2. Fix Missing Substantive Text**

I found one definite canonicalization gap:

```text
ID: schedule-X-3-3-formula
Expected text: A = B - C
Current canonical text: empty
```

The expression exists in raw [schedule10.json](/Users/avikalchauhan/incometax2025anti/incomeTax2025/txt_to_json/schedule10.json), but it was lost during canonicalization. Fix the Schedule canonicalizer so `expression` becomes the formula unit’s `text`.

An empty paragraph such as `schedule-XV-2` may be valid if it only groups child clauses. Do not index the empty parent independently.

**3. Improve Schedule Search Records**

Your current Schedule records are searchable, but their top-level search metadata lacks:

```json
{
  "schedule_id": "schedule-XV",
  "schedule_number": "XV",
  "schedule_title": "DEDUCTION IN RESPECT OF..."
}
```

These values currently exist only inside `metadata_json.source_properties`. Add them as normal fields in:

- `search_documents.jsonl`
- the SQLite `search_documents` table
- Qdrant payloads

This allows direct filtering by Schedule.

**4. Make Retrieval Text Self-Contained**

For a Schedule paragraph:

```text
Schedule XV
[Schedule title]
Paragraph (4)
[paragraph title]
[exact paragraph text]
```

For a Schedule table row:

```text
Schedule XV
[Schedule title]
Paragraph (4)
[paragraph title]
Table columns: Sl. No.; Nature of payment; Conditions for disallowance
Row 1
[exact labelled cell values]
```

Your rows already contain the parent citation, headers, row number, and values. The missing improvement is consistently including the full Schedule title.

**5. Decide What Is Searchable**

Index non-empty substantive units:

- paragraphs and subparagraphs
- clauses and nested clauses
- items
- table rows
- meaningful table-cell items
- notes and footnotes
- formulas and variables

Do not index:

- Schedule roots
- structural Parts with no operative text
- table containers
- empty paragraphs used only as parents

Your current search index contains all `164` Schedule rows, but only `219` of `228` table-cell items. Nine short cell items were excluded by the four-word rule. Review them because short legal values such as `Nil`, `10%`, or `Two kilometres` can still be meaningful. The complete row remains searchable even when its individual cell is excluded.

**6. Validate**

Create a Schedule-specific validation report that confirms:

1. Every canonical Schedule ID exists once in `graph_nodes.jsonl`.
2. Every non-empty substantive Schedule unit exists once in `search_documents.jsonl`.
3. Parent IDs exist and lead to the correct Schedule root.
4. Schedule metadata is consistent throughout each Schedule.
5. Every table row contains its column headers and values.
6. Formula expressions and variable definitions are preserved.
7. Notes and footnotes have valid `annotates` targets.
8. All resolved Schedule references point to existing nodes.

After fixing any issues, rebuild graph data, BM25, and the new Qdrant collection. Then Step 6 is complete and you can proceed to Step 7, combining BM25 and vector results with RRF.

## 7. Combine BM25 And Vector Results

Run both searches:

```text
BM25 top 30
Vector top 30
```

Merge them using Reciprocal Rank Fusion rather than directly adding unrelated scores.

Then retain approximately:

```text
20–40 initial candidates
```

Graph expansion and reranking happen in later steps.


## **After the embedding command completes, we do Graph expansion and reranking happen in later steps.?**

Yes, but first you must combine BM25 and vector results.

The correct order is:

```text
1. Embed and store documents in Qdrant
2. Receive user query
3. BM25 search → top 30
4. Qdrant vector search → top 30
5. RRF fusion → top 20–40 candidates
6. Graph expansion
7. Deduplicate and control context size
8. Rerank expanded candidates
9. Send final evidence to the LLM
```

**Graph expansion** uses `graph_edges.jsonl` to add necessary dependencies around each RRF candidate:

- parent provisions
- relevant children
- outgoing references
- incoming references
- Schedule targets
- notes or footnotes

Expansion must be selective. Do not add every sibling or every referenced provision automatically.

**Reranking** happens after expansion. A reranker evaluates the query against each candidate evidence bundle and keeps the most legally relevant results, commonly the top 8–15.

Therefore, the immediate next step after embedding is:

```text
Build BM25 + Qdrant search and RRF fusion
```

Graph expansion and reranking are subsequent implementation steps, not part of the embedding command.