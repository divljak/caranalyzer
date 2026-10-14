# Car Analyzer UI

The React, TypeScript and Vite workspace has four pages: **Market**, **Model analysis**, **Compare** and **Following**. The active app is `src/refined/Workspace.tsx`. Design references, shared tokens and illustrative data are in `../design/`.

## Preview with illustrative data

From the repository root, with Node.js 22.6 or newer:

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open http://127.0.0.1:8080/market?demo=1. Demo mode needs no database. It uses illustrative aggregate data, with separate browser storage for saved searches and comparison selections. Supported routes are `/market`, `/models/:make/:model`, `/compare` and `/following`; keep `?demo=1` when opening a demo route directly.

Append `&state=loading`, `&state=empty`, `&state=error` or `&state=stale` to inspect demo data states.

## Run against a local PostgreSQL database

The default mode reads the local API. It does not substitute illustrative prices when the API is unavailable.

Create a Python environment, install the API dependencies, and select a local PostgreSQL database. Replace the example connection below with your local connection or restored collector copy:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dealer.txt
export DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:5432/car_analyzer_local
.venv/bin/python -m database.models
npm --prefix frontend run build
.venv/bin/python -m uvicorn dealer_api:app --host 127.0.0.1 --port 8010
```

Open http://127.0.0.1:8010/market for the built app. The schema command creates collector tables and applies compatible column additions; API startup creates its supporting tables. An empty database has no collected market evidence. Select a local database, because startup writes labels and supporting tables there. Saved searches persist in that database.

For frontend development, keep the API running and start `npm --prefix frontend run dev` in a second terminal. Open http://127.0.0.1:8080/market. Vite proxies `/api` to port 8010, including the local origin handling needed for save/remove actions.

The API uses loopback host checks and cross-origin write protection. Public hosting requires authentication and user isolation before exposing this private workspace.

## Checks

From the repository root:

```sh
node --experimental-strip-types --test frontend/src/refined/evidence.test.ts
```

From `frontend/`:

```sh
npx tsc -p tsconfig.dealer.json --noEmit
npx eslint src/refined src/main.tsx vite.config.ts
npm run build
```

Backend parser checks can run without a database:

```sh
.venv/bin/python -m unittest discover -s tests -p test_market_labels.py
.venv/bin/python -m unittest discover -s tests -p test_listing_details.py
```

API integration tests require a separate, disposable PostgreSQL database with the schema initialized above. Set both `DATABASE_URL` and `DEALER_TEST_DATABASE_URL` to that test database before running:

```sh
.venv/bin/python -m unittest discover -s tests -p test_dealer_api.py
```

The checks target the active workspace. Older unused frontend modules have separate type/lint issues.

## Evidence

Typical price is the median asking price; bands show the middle 50%. Ads with missing prices are excluded from price statistics, repeated observations do not add weight, and price-cut counts represent distinct ads. The Market ceiling determines eligibility without clipping price distributions. Monthly history uses the last usable price per ad per UTC month. Following compares saved baselines only after a later collection.

Collected data has incomplete coverage. Asking prices and observations do not establish completed sales or resale speed, and a change in car mix can change the median.
