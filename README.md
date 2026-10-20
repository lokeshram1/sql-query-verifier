# SQL Query Verifier

SQL Query Verifier turns plain-English business questions into read-only SQL, executes it against a reproducible local SQLite sandbox, and independently checks whether its output reconciles with known data. It demonstrates an important failure mode: executable SQL can still produce an untrustworthy answer when a one-to-many join repeats order-level revenue.

All sample records are synthetic. Verification reports provide evidence and safeguards; they do not formally prove arbitrary SQL correct.

## Demo workflow

1. Start the API and frontend using the commands below.
2. Open `http://localhost:3000` and choose **Run revenue double-counting demo**.
3. The UI executes both the flawed query and its reference repair against seeded SQLite data. It displays output, the computed inflation, the failed reconciliation and fan-out evidence, and the reconciled reference result.
4. Use **Load flawed query** or **Review corrected query** to populate the editable SQL panel. Re-run only after reviewing the query.
5. Export the visible results as CSV or the verification report as JSON.

The demo data contains 5 customers, 4 products, 7 orders (including a cancelled order), and 12 line items. Customers have multiple orders and orders have multiple items. The seed runs once into `backend/data.sqlite3`.

## Architecture

- `backend/app.py`: FastAPI routes, Pydantic request models, deterministic seed, provider interface, SQL allowlist, read-only query execution, verification, and exports.
- `backend/test_app.py`: API and deterministic regression tests.
- `frontend/app/page.tsx`: responsive query workspace, schema explorer, trace, verification report, export actions, and live-executed demo.
- `frontend/app/style.css`: dashboard styles.

The mock provider is the default and selects repeatable query templates. Set `LLM_PROVIDER=openai` and `OPENAI_API_KEY` to use the optional OpenAI structured JSON provider; `OPENAI_MODEL` optionally selects the model. Only question and schema context go to the provider. Database rows remain local. Provider errors return a safe 502 response.

## Run locally

Requires Python 3.10+ and Node.js 20+.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. To build the UI, run `npm run typecheck` and `npm run build` in `frontend`. Run backend tests with `pytest -q` in `backend`.

To point the browser UI to a different API, set `NEXT_PUBLIC_API_URL` when starting Next.js. The API defaults to mock mode and does not need credentials.

## Release verification commands

From the project root, after Python and Node.js are installed and package downloads are available:

```powershell
cd backend
python -m pip install -r requirements.txt
python -m pytest -q
uvicorn app:app --reload --port 8000
```

In another terminal:

```powershell
cd frontend
npm install
npm run typecheck
npm run build
npm run dev
```

`requirements.txt` and `package.json` pin direct dependency versions. A frontend lockfile is not present yet; create and commit one with `npm install` when registry access is available to pin transitive versions as well.

## API

- `GET /api/health` – health and provider mode.
- `GET /api/schema` – tables and relationships.
- `POST /api/generate` – `{"question":"What was total revenue by customer segment?"}`.
- `POST /api/run` – `{"question":"...","sql":"SELECT ..."}`.
- `POST /api/demo` – executes both critical demo queries.
- `POST /api/export/csv` and `/api/export/json` – generate downloads from supplied result/report JSON.

All query output is bounded to 500 rows. SQLite is opened in read-only URI mode with `query_only`; a SQLite authorizer permits reads only from the four seeded tables and denies writes, pragmas, attach/detach, and dangerous file or extension functions. A progress handler caps SQLite VM operations (it is an operation budget, not a wall-clock deadline). SQL must be one SELECT or WITH query. Errors are returned without tracebacks.

## Example questions

- What was total revenue by customer segment?
- What was total revenue by month for the last three months?
- Show me revenue by segment.

## Limitations and trade-offs

The mock generator intentionally covers the demo and common sample question patterns. Optional OpenAI generation is not required by core functionality. The fan-out check combines a demo-specific repeated-order-join signal with an independently computed segment revenue baseline. A baseline is only applicable when results expose `segment` and `revenue`; general SQL grain inference is inherently limited. SQL validation is a defense-in-depth allowlist backed by SQLite read-only mode, not an arbitrary-query proof system. The demo is a local single-process MVP without accounts, persistence beyond its seed database, or external data ingestion.
