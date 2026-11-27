# My Sign vs the Golden Path

Audit against `.claude/skills/apply-engineering-guidelines` (soofi-xyz team kit
0.30.1). The guidelines state that deviation requires justification in peer
review — this is that justification, plus the list of things that are simply
missing.

| Standard | My Sign | Status |
| --- | --- | --- |
| TypeScript for all services | Python (FastAPI) | ❌ **CRITICAL — decision needed** |
| LLM via Vercel AI SDK, no provider SDKs | `anthropic` SDK directly | ❌ **CRITICAL — follows from the above** |
| AWS, primary region `us-east-2` | defaulted `us-east-1` | ✅ fixed |
| Infrastructure as CDK | CDK | ✅ |
| Pytest + CI in GitHub Actions | 39 pytest tests, no CI | ✅ CI added |
| Ruff / basedpyright | both configured, ruff in CI, clean | ✅ closed |
| Powertools logging, tracing, metrics | structured JSON logs, EMF metrics, X-Ray | ✅ closed |
| PagerDuty on every critical failure | `app/alerts.py`, incl. red-tier safety | ✅ closed |
| One self-resolving alarm per DLQ | cron DLQ + 3 alarms → SNS | ✅ closed |

---

## The two CRITICAL ones are the same decision

**`stack-typescript-for-apis`** requires TypeScript for all services, with Python
permitted *only* for PySpark and Glue. **`stack-ai-sdk-for-llm`** requires the
Vercel AI SDK and forbids provider SDKs — and mandates TypeScript for all
LLM-calling code. The second falls out of the first.

My Sign is Python for one reason: **Swiss Ephemeris**. `pyswisseph` wraps the
reference C implementation and is what makes the engine's central promise
enforceable — the model never computes a planetary position, it receives one from
a table we computed. Everything downstream (salience, pattern verification,
context assembly) grew around that.

Three ways out, in increasing cost:

**1. Deviate, and write it down.** Keep Python; cite the ephemeris as the
justification the guidelines explicitly allow for. Cheapest, and honest. The cost
is that My Sign never shares tooling, Powertools conventions, or CI templates with
the rest of the estate — every future engineer pays a small tax forever.

**2. Split the boundary.** TypeScript API and LLM layer per the Golden Path; a
single Python Lambda that does nothing but chart math behind a typed contract.
Compliant everywhere it can be, and the deviation shrinks to one function with an
obvious reason. Costs a rewrite of `app/` and `engine/context.py` — roughly a
week — and adds a network hop to every reading.

**3. Port the ephemeris.** All TypeScript. Requires replacing `pyswisseph` with a
JS ephemeris, and the candidates are not equivalent: accuracy, house systems, and
maintenance status all differ. **This is the option that can quietly break the
product**, because a subtly wrong chart still renders fine. If it is taken, it
needs a differential test — thousands of dates cross-checked against Swiss
Ephemeris — before anything ships.

Recommendation: **(1) now, (2) when a second engineer joins.** The reason to
comply is shared tooling across a team, and there is no team yet. Revisit the day
that changes, not before.

## PagerDuty and the red tier

The guidelines require paging on-call for every critical failure. My Sign has a
category the rest of the estate does not: a **red-tier safety event** — someone in
crisis. That is not a system failure, so it does not fit the DLQ-alarm pattern,
but it is the single most time-sensitive event the product can produce.

It should page. Same mechanism, different trigger: `engine/safety.py` returning
`red` fires a PagerDuty event alongside the canned reply. Nobody should learn
about it from the nightly job.

## What is cheap and still open

- **Ruff + basedpyright** — config plus a CI step. Half a day.
- **Powertools** — no Python Powertools equivalent is wired up. Structured JSON
  logs with a request id would cover most of the value now; X-Ray needs the CDK
  change too.
- **DLQs** — the cron Lambda has no dead-letter queue. A failed morning push
  currently vanishes into CloudWatch. This is the gap most likely to bite during
  the pilot, because a silent push failure looks exactly like a quiet day.


---

## Closed since this audit was written

- **Ruff and basedpyright** are configured in `pyproject.toml`, and `ruff check`
  runs in CI as its own job. Getting to clean found a real bug: `window_days`
  was read in the pattern verifier and never used, so a pattern proposed as
  "the two days before the full moon" was silently checked against the exact day
  only. Real patterns were being under-counted and rejected.

- **Observability.** Structured JSON logs with a request id on every line,
  CloudWatch EMF metrics (no SDK, no PutMetricData in the request path), and
  X-Ray tracing on the web function. Metric dimensions are deliberately
  low-cardinality — one metric per user is a bill, not an insight.

- **CI tests two Pythons.** 3.11 is the Lambda runtime; 3.9 is the floor a
  contributor might have. Testing only the ceiling let an auto-fixer rewrite
  `dt.timezone.utc` to the 3.11-only `dt.UTC` — CI stayed green while onboarding
  was broken for anyone on an older Python.

## Still open, and why

**The TypeScript question is unchanged.** Swiss Ephemeris is the reason this is
Python, and nothing above alters that. It remains the one deviation that needs a
decision rather than a fix.
