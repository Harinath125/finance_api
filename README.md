# K&I Wealth Tech — Financial Opportunity Processing & Eligibility API

This is my submission for the Python engineering assessment. It's a small
FastAPI service that reads the opportunity dataset, cleans it up, checks
which opportunities a user is eligible for based on their investment
criteria, ranks the eligible ones, and returns everything through a single
search endpoint.

## Setup
Extract Harinath_Python_Assignment.zip

In Terminal/Command prompt:

cd "path of the folder Harinath_Python_Assignment"

Needs Python 3.10+ (I used `list[str]`-style type hints in a few places,
which require it).

```bash
python3 -m venv venv

source venv/bin/activate          # Windows: venv\Scripts\activate
macOS\linux: source venv/bin/activate  #tested on mac

pip install -r requirements.txt
```

## Running it

```bash
python -m src.main
```

Starts on `http://localhost:8000`.
 Swagger docs are at
`http://localhost:8000/docs`(use this for best results) if you want to try requests from the browser
instead of curl.

For development with auto-reload:

```bash
uvicorn src.api:app --reload
```

## Trying a search

```bash
curl -X POST http://localhost:8000/opportunities/search \
  -H "Content-Type: application/json" \
  -d '{
        "investment_amount": 200000,
        "risk": "Moderate",
        "tenure_months": 24,
        "minimum_return": 9,
        "category": "Debt",
        "liquidity": "Medium"
      }'
```

I've saved the request/response for this exact example under
`sample_output/`.

There's also a `GET /health` endpoint that just confirms the API is up and
shows how many opportunities loaded.

## Running the tests

```bash
pytest tests/ -v
```

Tests are split by layer: `test_validator.py` for the parsing/normalization
functions, `test_eligibility.py` and `test_ranking.py` for the business
rules (no HTTP involved), and `test_api.py` for the actual endpoint,
including the sample request from the brief and the error cases in
section 4.5.

## How it's organized

Harinath_python_assignment/
│
├── README.md              ← START HERE. Plain-English "how to run this"
├── TECHNICAL_NOTE.md       ← Read SECOND. Explains *why* i built it this way
│
├── data/                   ← The raw ingredients (given to you, not written by us)
│   ├── opportunities.csv       the 30 messy investment records
│   ├── sample_queries.csv      example searches to test with
│   └── data_schema.json        what fields exist and their allowed values
│
├── src/                    ← The actual program, read in THIS order:
│   ├── models.py                defines what an "Opportunity" and a "Search Request" look like
│   ├── data_loader.py            step 1: just reads the CSV file into memory,that's it
│   ├── validator.py              step 2: cleans up the messy data (currency,tenure, risk, return)
│   ├── eligibility.py            step 3: decides who qualifies
│   ├── ranking.py                step 4: scores the qualifiers
│   ├── api.py                    step 5: wires it all into a web server
│   └── main.py                   the "on" switch — starts the server
│
├── tests/                  ← Automated checks that prove each piece works
│   ├── test_validator.py
│   ├── test_eligibility.py
│   ├── test_ranking.py
│   └── test_api.py
│
├── sample_output/           ← A saved example of a real request/response
├── requirements.txt         ← List of external libraries needed (pip reads this)

I kept the HTTP layer (`api.py`) and the actual business rules
(`validator.py` / `eligibility.py` / `ranking.py`) separate on purpose —
the rules can be unit-tested with plain Python objects, no server needed.

## A few decisions worth flagging

- Nothing gets silently dropped during validation. If a field won't parse,
  it's recorded as a `ValidationIssue` and shows up in the response's
  `validation_warnings`, rather than the row just disappearing.
- Category and liquidity are treated as soft preferences that affect
  ranking, not hard eligibility filters — the brief lists them as optional
  under eligibility *and* as a ranking factor, so I went with "influences
  score, doesn't exclude." Explained in more detail in TECHNICAL_NOTE.md.
- When a minimum return is requested, opportunities with an unparseable or
  `"Variable"` return are excluded rather than assumed to pass — didn't
  want to guess eligibility off missing data.

More on the reasoning behind all of this is in TECHNICAL_NOTE.md.
