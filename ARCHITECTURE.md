# Architecture and trust boundaries

## Generation and verification are separate

The provider sees the business question and declared schema. Its output is treated as untrusted text and is parsed into structured metadata. A mock provider is the default; an optional server-side OpenAI provider uses structured JSON output. Neither provider is asked to inspect database records. The deterministic verifier runs after SQL execution and does not use model confidence as evidence.

## SQL boundary

Every submitted statement passes through a read-only SELECT/WITH allowlist. Multiple statements and write, DDL, attach/detach, pragma, extension, and file-operation tokens are rejected. A SQLite authorizer additionally permits reads only from the four seeded tables and allows only read-oriented SQLite actions; this is the enforcement boundary if SQL syntax evades a text check. Queries execute on a read-only SQLite connection with `query_only` and a progress callback, returning at most 500 rows. The progress callback caps SQLite VM operations rather than elapsed wall-clock time. SQLite validates table and column names. API errors are bounded and do not expose stack traces. This MVP is intended for its bundled sandbox and is not a general-purpose hostile multi-tenant SQL service.

## Fan-out and reconciliation

The reference revenue grain is paid order items summed once by customer segment. The flawed demo pre-aggregates revenue by order and then joins orders again by customer, multiplying each order's amount by the customer's number of orders. The report shows counts from the real seeded order relationships, checks output totals against an independent SQL aggregate, and reports missing or extra segment keys. A legitimate increase in rows is not itself treated as a failure. The specific repeated-order join signal is an explicit demo heuristic, not a universal SQL lineage engine.

Baseline comparisons use a one-cent tolerance for seeded monetary totals. Null rates and row-count sanity are surfaced independently. Checks that do not apply are labeled inconclusive/N/A with a reason. Verification gives users concrete evidence and safeguards, not a formal proof of correctness for arbitrary SQL.
