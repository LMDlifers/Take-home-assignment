# Design Notes

## Data Source Of Truth

The app treats PostgreSQL as the executable source of truth. The LLM may route, generate read-only SQL, and explain retrieved data, but it should not invent operational records.

Known differences from `Documents/GenAI_Agentic_Test.pdf`:

- Machine load: the PDF key highlights M3, M6, and M5. The active seed also overloads M1 and M7, and M3 calculates to 195.0% because queued hours sum to 19.5 against 10.0 capacity.
- Priority window: the PDF key omits WO-1019 and some P2 rows. The API reports live rows that match the current priority and due-window filters.
- Date-sensitive results depend on when the Postgres volume was first seeded because `seed.sql` uses `CURRENT_DATE`.

## Agent Shape

The `/api/v1/ask` flow keeps one visible route:

1. Parse obvious IDs and intent directly, such as `WO-1003`, `M2`, or `down 4 hours`.
2. Ask Qwen for structured routing when wording is fuzzy, such as `machine beta`.
3. Verify extracted work orders and machines against PostgreSQL.
4. Route to one tool: `run_sql`, `check_load`, `simulate_downtime`, `get_priority`, `recommend`, or `refuse`.
5. Build a deterministic answer from returned evidence.
6. Ask Qwen for a plain-English explanation from the same evidence.
7. Attach `trace` and write `agent_action_log`.

`answer` stays deterministic so key IDs, statuses, and recommended actions cannot be dropped during prose rewriting. `explanation` remains LLM-written because that is where the assignment asks for planner-friendly language.

## Confidence

Confidence is a simple source-based score:

- Deterministic tool or fixed SQL path: `0.92`
- Generated SQL success: `0.75`
- Fallback or refusal paths keep their explicit lower confidence

This is intentionally boring. A second LLM judge added latency and complexity without improving the required assignment behavior.

## Future Polish

The lean routing layer handles the assignment questions and common variants, but the latest manual run showed a few cheap improvements:

- Treat shorthand priorities like `P1` and `P2` as scheduling terms.
- Treat `bay`, `location`, `product`, and `family` as in-scope lookup terms.
- Route "what should I do about M4?" style questions to `recommend`.
- Improve one-row status summaries when generated SQL returns generic shapes.
