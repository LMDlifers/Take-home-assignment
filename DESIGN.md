# Design Notes

## Assessment Data Mismatches

This implementation treats the PostgreSQL seed as the executable source of truth and records known differences from `Documents/GenAI_Agentic_Test.pdf`.

- Machine load: the PDF key highlights M3, M6, and M5 as overloaded. The active seed also overloads M1 and M7, and M3 calculates to 195.0% because the current queued hours sum to 19.5 against 10.0 capacity. The API reports the live calculation rather than hiding those rows.
- Priority window: the PDF key omits WO-1019, but the active seed can include it because it is P1 and falls inside the current due-date window. The deterministic helper keeps the assignment-oriented P1 filter and the conformance notebook records the ambiguity.
- Work-order risk: direct WO-risk questions use `v_at_risk_orders` deterministically, then the LLM explains returned data. This avoids flaky SQL generation for known assessment checks while keeping the `/api/v1/ask` response shape unchanged.
