At query time, run both searches against the **same set of canonical units**:

1. Send the user’s question to the SQLite BM25 index. Keep the top 30 results.
2. Embed the question with the **same Qwen model** used for your documents, then search Qdrant. Keep its top 30 results.
3. Match results by canonical `id`, which must be identical in SQLite and Qdrant.
4. Combine their **ranks**, rather than their raw scores. For each result, add `1 / (60 + rank)` from each list where it appears. This is Reciprocal Rank Fusion (RRF).
5. Sort by the combined score and keep roughly 20–40 candidates.

For example, if `36-3-a-i` ranks 2nd in BM25 and 5th in Qdrant, its score is `1/62 + 1/65`. A unit found by only one search still gets that search’s contribution.

Before implementing this, confirm the Qdrant collection contains embeddings for the **current** `search_documents.jsonl`. Your latest BM25 rebuild has 8,912 documents; the earlier `v2` collection may represent an older set. Then test the fused results with exact section citations, legal phrases, and plain-language questions. Graph expansion comes after fusion.


# **problem of citastion quries**

For Section 36(3)(a)(i), BM25 found the exact unit at rank 3. Both searches also ranked several table rows inside that unit highly. Those rows gained strong combined RRF scores, so the exact unit ended at fused rank 6. This is a problem for citation queries: the user named a precise provision and should see that provision first.