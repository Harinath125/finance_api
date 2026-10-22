# Technical Note

## 1. Architecture

I followed the suggested file layout pretty closely. The dependency
direction only goes one way:

```
api.py  ->  eligibility.py  ->  models.py
        ->  ranking.py      ->  eligibility.py
        ->  validator.py    ->  models.py
        ->  data_loader.py
```

`data_loader.py` just reads the CSV into a list of dicts, no parsing.

`validator.py` turns those raw dicts into proper `Opportunity` objects.

`eligibility.py` and `ranking.py` only work with those objects and a
`SearchRequest` — they don't know anything about HTTP or CSVs at all.

`api.py` is the thin layer on top: load the data at startup, call the two
engines, map the result onto the response schema.
-----> The upside of doing it this way is that all the actual business logic can be tested with plain
objects, no server running (see `test_eligibility.py` / `test_ranking.py`).

The dataset loads once at startup and stays in memory for the life of the
process. For 30 rows that's obviously fine 
—----> a real version would need some kind of refresh (scheduled pull(for less number of records), webhook, or at least an admin reload endpoint) instead of a static CSV.

## 2. Normalizing the data

Four fields needed real parsing, based on what's actually in `opportunities.csv`:

- **Currency** (`normalize_money_to_inr`) — strips out `₹`/`Rs.`/`INR` and
  the Indian-style comma grouping, then handles `lakh` (x100,000) and
  `crore` (x10,000,000). Covers both `"₹1,00,000"` and `"Rs. 2 lakh"`
  style values from the dataset.

- **Tenure** (`normalize_tenure_to_months`) — handles a single value, a
  range like `"12-24 months"`, years (`"3 years"` -> 36 months), and
  open-ended tenure (`"24 months+"` -> min 24, max `None` because we can't assume cap value).

- **Risk** (`normalize_risk`) — maps whatever wording shows up in the data
  onto one 5-point scale: `Low < Low-Moderate < Moderate < Moderate-High < High`. 
  ----->There are two synonyms in the dataset that aren't in the
  documented schema — `"Medium"` on OPP002 and `"Moderately High"` on
  OPP005 — I mapped both explicitly instead of letting them fail.

- **Return** (`normalize_return`) — parses `"9.2% p.a."` and ranged values
  like `"10.1-11.0% p.a."`.
  ----->  `"Variable"` (OPP014) isn't a parse failure —
it's a real value meaning "no fixed number given", so it's logged as a
  warning rather than an error, and treated differently from text that's
  actually garbled.

My general rule for validation: don't drop anything silently. Every row
gets normalized as far as it can be, and anything that doesn't parse
becomes a `ValidationIssue` (error or warning) attached to that record,
which then shows up in the API's `validation_warnings`. A record only gets
excluded from eligibility/ranking (`is_valid = False`) if one of the
fields the engines actually depend on — id, minimum investment, tenure,
risk — failed to parse, or if it's a later duplicate of an ID already seen
(`is_duplicate` — that's OPP030 in this dataset).

## 3. Eligibility rules

Hard filters — an opportunity has to pass all of these:

1. `investment_amount >= minimum_investment`
2. `opportunity.risk <= user.risk` (comparing positions on the 5-point scale)
3. `user.tenure_months` falls inside `[tenure_min, tenure_max]` (no upper
   bound if `tenure_max` is `None`)
4. If a `minimum_return` was requested, the opportunity needs a known
   return figure whose upper end clears that minimum. If the return is
   unsupported/`"Variable"`, I exclude it rather than assume it's fine —
   felt safer than guessing eligibility off missing data.

Soft signals — these never exclude an opportunity, only affect its score:

- `category` and `liquidity`. The brief mentions these twice: once as
  "optional preferences" under eligibility, and again as a ranking factor.
  I went with "ranking-only" for both, since making them hard filters
  would mean by the time ranking runs, everyone left already matches
  anyway — the ranking weight for them wouldn't do anything. Treating them
  as scoring signals means an opportunity that's a great fit apart from,
  say, one liquidity tier, still shows up — just ranked a bit lower
  instead of vanishing. I could see an argument for the opposite reading,
  so flagging it here as a decision I made rather than something the brief
  states outright.

If nothing is eligible, the API returns `count: 0, results: []` — that's
a normal outcome, not an error.

## 4. Ranking

I went with five weighted components that add up to a 0-100 score (the
actual code and reasoning is in `ranking.py`):

| Component          | Weight | Why |
|--------------------|--------|-----|
| Risk fit           | 30%    | Rewards opportunities whose risk is close to what the user said they'd tolerate — using more of that budget rather than staying overly conservative for no reason. |

|Tenure fit          | 25%    | A tighter tenure window scores higher than a wide one, since a wide window fits almost any request and isn't really a "fit" so much as a non-exclusion. |

| Return             | 25%    | Normalized against the best average return *within the eligible results for this query*, not the whole dataset — so it's relative to what's actually on the table for this search. |

| Budget efficiency  | 10%    | Rewards opportunities that don't eat up as much of the stated investment amount, leaving room to diversify. |

| Preference bonus   | 10%    | Rewards matching the optional category/liquidity preferences, when the user gave any. |


## Ranking formulas

Each component below produces a value between 0 and 1 (or a neutral 0.5
when the underlying data is missing/unparsed). The final score is:

score = 100 × (0.30 × risk_fit
             + 0.25 × tenure_fit
             + 0.25 × return_score
             + 0.10 × budget_efficiency
             + 0.10 × preference_bonus)


**Risk fit (30%)** — rewards using more of the user's stated risk budget.
gap = user.risk.rank - opportunity.risk.rank     # rank: Low=0 ... High=4
risk_fit = 1.0 - (gap / 4)

Unknown opportunity risk → 0.5.

**Tenure fit (25%)** — rewards a tighter tenure window.

width = tenure_max - tenure_min                  # 36 if open-ended
tenure_fit = 1.0 / (1.0 + width / 12.0)

Unparsed tenure → 0.5.

**Return score (25%)** — normalized against the best return on offer for this query.

return_score = opportunity.expected_return_avg_pct / best_avg_return_in_eligible_set

No known return → 0.

**Budget efficiency (10%)** — rewards leaving more capital free to diversify.

budget_efficiency = 1.0 - (opportunity.minimum_investment_inr / user.investment_amount)

No stated minimum → 1.0.

**Preference bonus (10%)** — rewards matching optional category/liquidity preferences.

preference_bonus = matched_preferences / stated_preferences

No preferences stated → neutral default (no bonus/penalty).



The brief says there's no single right formula, so I just tried to make
mine easy to justify and easy to change — the weights live in one place
(`ranking.WEIGHTS`), and every component that contributes to a score also
adds its own line to that result's `reasons`, so nothing is a black box.

I'll admit the weights themselves (30/25/25/10/10) are a judgment call —
risk and tenure felt like the two things a user is most likely to care
about getting exactly right, return next, and the last two as smaller
tie-breakers. Reasonable people could weight this differently.

## 5. Assumptions I made

- Category matching is exact (case-insensitive) — `"Debt"` won't match
  `"Corporate Debt"`. A fuzzier or hierarchical match seemed out of scope
  without a defined category taxonomy to work from.
- For `minimum_return`, eligibility checks the opportunity's *best-case*
  (max) return, but ranking scores off the *average*. The way I thought
  about it: eligibility is asking "could this possibly satisfy what they
  asked for," ranking is asking "how good a fit is this overall" — those
  are different questions and I think they warrant different numbers.
- Duplicate IDs: I keep the first occurrence and flag/exclude the later
  one, on the assumption the first row is the canonical one. The brief
  doesn't say which to keep, so this was my own call.

## 6. Edge cases I handled

Negative or zero investment amount, invalid risk/tenure values, malformed
JSON, missing required fields — all of these return HTTP 422 with a
structured error body rather than a stack trace. No matching opportunities
returns HTTP 200 with an empty result, not an error. Invalid source
records and duplicate IDs get excluded from results and reported as
warnings instead of crashing the whole load.

## 7. What I'd add for a real production version

- A real data source instead of a static CSV, with a proper refresh path.
- Pagination on `/opportunities/search` — fine for 30 rows, wouldn't scale.
- Structured logging, request tracing, and some kind of auth on the endpoint.
- Property-based tests (Hypothesis, maybe) for the parsers, to catch
  formats beyond what's in this sample dataset.
- Making the ranking weights configurable instead of hardcoded, so they
  can be tuned without a code change.
