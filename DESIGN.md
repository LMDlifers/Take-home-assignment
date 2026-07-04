# Design Notes

## Assessment Data Mismatches

This implementation treats the PostgreSQL seed as the executable source of truth and records known differences from `Documents/GenAI_Agentic_Test.pdf`.

- Machine load: the PDF key highlights M3, M6, and M5 as overloaded. The active seed also overloads M1 and M7, and M3 calculates to 195.0% because the current queued hours sum to 19.5 against 10.0 capacity. The API reports the live calculation rather than hiding those rows.
- Priority window: the PDF key omits WO-1019 and some P2 rows. The active seed can include them because they fall inside the current high-priority/week window. The API reports the live calculation and the conformance notebook records the ambiguity.
- Work-order risk: direct WO-risk questions use `v_at_risk_orders` deterministically. This avoids flaky SQL generation for known assessment checks while keeping the `/api/v1/ask` response shape unchanged.

## Hybrid Routing

The `/api/v1/ask` flow now uses a small deterministic layer before tool execution:

1. Parse obvious IDs and intent directly, for example `WO-1003`, `M2`, `1003 current status`, or `down 4 hours`.
2. Ask DeepSeek-R1 for visible `<think>` router reasoning and structured extraction, especially for fuzzy wording such as `machine beta`.
3. Verify extracted work orders and machines against PostgreSQL before routing.
4. Route from the structured intent to deterministic tools where possible; use generated SQL only for open-ended lookup questions.

This keeps the app dynamic without letting the model invent operational records. The model can propose an entity or intent, but PostgreSQL remains the source of truth.

The `/ask` response separates `answer` from `explanation`: `answer` is built
from retrieved data, while `explanation` is the parsed DeepSeek-R1 reasoning
block used for routing/backtesting.
