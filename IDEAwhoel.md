Yes. That is the correct pipeline.

```text
JSON → Graph + Index → Retrieve → Expand → Rerank → LLM
```

Use it like this:

**1. JSON**

Your raw structured law JSON.

```text
sections
subsections
clauses
tables
rows
cell items
schedules
```

Do not overload this with RAG logic. Keep it as source data.

**2. Graph + Index**

From JSON, create two things.

Graph:

```text
Section 10 → contains → Section 10(a)
Section 19(1) → contains → Table
Table → has_row → Sl. No. 2
Section 59(4) → references → Section 62
```

Search index:

```text
BM25 index
vector index
metadata index
```

Each canonical unit should stay small:

```text
text = exact unit text
retrieval_text = compact searchable text
metadata = section, type, page, chapter
```

**3. Retrieve**

Given a user query, search:

```text
BM25 + vector search
```

Return top candidates only.

Example:

```text
query: standard deduction salary
retrieved: Section 19(1) Table, Sl. No. 2
```

**4. Expand**

Use the graph to add necessary context.

If retrieved:

```text
Section 19(1) Table, Sl. No. 2
```

Expand to:

```text
parent: Section 19(1)
table header
row 2
referenced Section 202(1), if needed
```

If retrieved:

```text
Section 10(b)
```

Expand to:

```text
Section 10 title
section intro
Section 10(a), because 10(b) refers to clause (a)
Section 10(b)
```

Do not expand everything blindly.

**5. Rerank**

After expansion, rerank candidates using a cross-encoder/reranker.

Purpose:

```text
remove weak matches
keep legally relevant units
```

**6. LLM**

Send only final compact legal context.

Answer rules:

```text
answer only from retrieved context
cite section/subsection/table row
say insufficient context if missing
do not invent law
```

So your canonical JSON should not carry the whole final context. It should support this pipeline.

Best canonical unit design:

```json
{
  "id": "10-b",
  "citation": "Section 10(b)",
  "unit_type": "clause",
  "parent_id": "10",
  "section_number": "10",
  "title": "Apportionment of income between spouses governed by Portuguese Civil Code.",
  "text": "the income mentioned in clause (a) ... shall be divided equally...",
  "retrieval_text": "Section 10(b) Portuguese Civil Code community property Goa income other than salaries divided equally husband wife",
  "metadata": {
    "act": "Income-tax Act, 2025",
    "jurisdiction": "India",
    "chapter": "",
    "page_start": 1,
    "page_end": 1
  }
}
```

Then graph edges separately:

```json
{
  "from_id": "10-b",
  "to_id": "10",
  "edge_type": "parent"
}
```

```json
{
  "from_id": "10-b",
  "to_id": "10-a",
  "edge_type": "references",
  "reference_text": "clause (a)"
}
```
# Dependency retrieval stage.
Mechanism	How it finds indirect relevance
For your system, I would add a dependency retrieval stage after initial hybrid search.
Mechanism	How it finds indirect relevance
Parent expansion	Retrieves introductions, conditions, and exceptions governing a matching clause
Outgoing references	Retrieves provisions explicitly cited by the matching unit
Incoming references	Finds other provisions that refer to the retrieved section, including possible restrictions
Definition lookup	Retrieves definitions of legally significant terms used in the evidence
Applicability lookup	Checks provisions governing the taxpayer category, tax year, chapter, or subject
Targeted follow-up search	Searches for missing eligibility conditions, exclusions, limits, and exceptions


Incoming references are particularly useful. Suppose clause B says “the deduction under section A shall not be allowed where…”. Search retrieves A, but A never mentions B.
Your graph already records:
B ── references ──→ A
Looking up who references A brings B into the candidate evidence. You can support this with an incoming-edge index; you do not have to duplicate every reference as another stored edge.
For dependencies without an explicit citation, you need an additional layer.
You can record relationships such as:
Definition D ── defines term used in ──→ A
Exception B  ── restricts ────────────→ A
Limit C      ── caps ────────────────→ A
These relationships need evidence and scope. A definition may apply only within one section or chapter. A general rule may affect hundreds of provisions, so storing its scope can be more practical than creating hundreds of individual edges.
LLMs can propose these relationships, but an inferred relationship should carry its supporting passage and review status. A plain citation edge does not establish whether the cited provision restricts, defines, or overrides another provision.
Your retrieval flow would become:
Question
   ↓
Hybrid search
   ↓
Parents + outgoing and incoming references
   ↓
Scoped definitions and applicability checks
   ↓
Targeted searches for missing conditions or exceptions
   ↓
Select evidence, preserve necessary dependencies, deduplicate
   ↓
LLM answer


That is the cleaner design.