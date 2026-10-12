# LangSmith tracing for legal RAG

The live pipeline can send one nested LangSmith trace per question. Tracing is
opt-in because the runs contain the question, retrieved statutory text, selected
evidence, model prompts, draft answers, and repair attempts. Do not submit
personal taxpayer data unless your LangSmith workspace and retention settings
are approved for it. API keys are read from environment variables, never added
to trace inputs.

Set `OPENAI_API_KEY` and `LANGSMITH_API_KEY` in the shell. If your LangSmith key
belongs to multiple workspaces, also set `LANGSMITH_WORKSPACE_ID`. Choose an
OpenAI model available to your account that supports structured outputs:

```bash
export OPENAI_MODEL="your-structured-output-model"
/opt/anaconda3/envs/rag/bin/python \
  /Users/avikalchauhan/incometax2025anti/incomeTax2025/hybried_search_pythoncode/live_legal_retrieve.py \
  --answer --answer-model "$OPENAI_MODEL" \
  --langsmith --langsmith-project income-tax-2025-rag \
  "What deduction percentage is allowed for provision for bad debts of a scheduled bank?"
```

Omit the positional question for an interactive session; type `exit` to stop.
Without `--langsmith`, the script makes no LangSmith calls. Without `--answer`,
it traces retrieval only and does not call OpenAI.

The trace tree is:

```text
legal_rag_question
  hybrid_retrieval       BM25 and vector ranks, RRF candidates
  graph_expansion        parents, references, candidate bundles, gaps
  reranking              bundle scores and evidence text
  evidence_selection     retained units, omitted units, context budget
  grounded_answer        final structured answer or abstention
    OpenAI Responses     draft; second call only if validation requests repair
```

Review the `income-tax-2025-rag` project in LangSmith. Check evidence precision
and required-unit recall separately from legal correctness of the answer.
LangSmith tracing records the run but does not itself certify citations,
definition scope, exceptions, or effective dates. The local validator checks
literal provenance and can abstain; a human reviewer must decide whether each
claim is legally supported. No live API request is made by the offline tests.
