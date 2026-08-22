# TripShield

A **Travel Recovery Orchestrator** for American Express Card Members. An independent
concept prepared for the **AMEX AI Hackathon 2026** — not an official American Express
product, and not affiliated with or endorsed by American Express.

## The idea

When a flight is cancelled, most tools re-price the flight. But the hotel, the airport
transfer, the park ticket and the dinner reservation for that trip were charged to the
same Card, so the disruption has a cost the airline never sees — and a structure the
airline cannot know about.

TripShield reconstructs the trip as a **dependency graph**, propagates the cancellation
through it to find what actually broke, delegates the repairs to specialized agents,
assembles whole candidate plans, ranks them against the member's own revealed
preferences, and then executes the approved plan one transaction at a time — with a
rollback path that asks each supplier what it would really give back.

## The scenario

Singapore → Tokyo → Osaka, 18–23 September 2026. Seven bookings, one Card.

```
                    SQ638  SIN → NRT
                    18 Sep 09:00 → 17:05        ← cancelled 06:02
                            │
        ┌───────────────────┼───────────────────┐
        │ HARD 1h           │ SOFT 2h30         │ HARD 10h
        ▼                   ▼                   ▼
  Narita Express      Sushi counter        Tokyo Disneyland
  18:15 → 19:20       18 Sep 20:00         19 Sep 09:00
        │                                       │
        │ HARD 45m                              │ HARD 3h
        ▼                                       ▼
  Hilton Tokyo Bay ─── SOFT 0m ──────►    GK205 NRT → KIX
  18–21 Sep                                21 Sep 14:00
                                                │
                                                │ HARD 1h30
                                                ▼
                                          Hotel Granvia Osaka
                                          21–23 Sep
```

Doing nothing costs **SGD 1,070** in non-refundable spend across all seven bookings —
a number no single supplier is in a position to tell the member.

## The workflow

| # | Stage | What happens |
| --- | --- | --- |
| 1 | **Detect** | Every upcoming flight is swept through a flight-status API. Nobody reports anything. |
| 2 | **Reconstruct** | The booking history rebuilds the itinerary and the edges between its parts. |
| 3 | **Assess** | The cancellation is propagated. Hard violations invalidate; soft violations degrade. |
| 4 | **Create tasks** | One per affected booking, carrying the real constraint its own edge demands. |
| 5 | **Delegate** | Flight AI runs first; Accommodation, Activity, Dining and Ground AI then assess validated inventory concurrently. |
| 6 | **Assemble** | Options are combined into *whole* candidate plans, one per strategy per flight. |
| 7 | **Recommend** | Server metrics define the eligible plans; Recommendation AI personalizes their order, with a deterministic fallback. |
| 8 | **Adjust** | The member rearranges by hand; the server revalidates every change. |
| 9 | **Approve** | The plan is frozen into an immutable snapshot and queued. |
| 10 | **Execute** | One transaction at a time, in dependency order, each charge authorised on its own. |
| 11 | **Compensate** | Rollback quotes every committed step before reversing anything. |

## Three decisions worth arguing about

**Severity, not just order.** A violated *hard* edge invalidates the booking it points
at; a violated *soft* edge only degrades it. Missing the dinner costs a deposit. Missing
the transfer means never reaching the hotel. Modelling both as "B comes after A" loses
the only distinction that matters when deciding what to spend money fixing.

**Plans are assembled whole, not stapled together.** Asking each agent for its single
best option and combining the results produces a plan nobody chose — the cheapest flight
beside the least-disruptive hotel change beside the fastest transfer — with totals that
describe no trip the member could take. Each candidate here is built end to end for one
objective.

**Refunding a ticket returns the money but not the day.** A plan that cancels the park
passport looks cheaper, faster and less disruptive than one that re-dates it, so without
a term for it the optimizer wins by quietly deleting the trip. `experience_lost` is a
fourth objective on the Pareto front — see below for how it is priced.

## Personalization

TripShield now has a bounded multi-agent recommendation path. Flight AI assesses
the root replacement inventory first. Once the server recalculates what each
arrival breaks, Accommodation, Activity, Dining and Ground AI agents run as four
batched concurrent calls. The server rejects unknown IDs and assembles only plans
that pass the dependency graph. A final Recommendation AI uses the member's
synthetic choice history and validated specialist findings to order the eligible
whole-trip plans and explain the trade-offs.

The deterministic time-value, switching-cost and reliability weights remain as
auditable metrics and the complete fallback. A model cannot alter supplier facts,
feasibility, metric values, approval or execution. If one specialist fails, only
that specialty falls back; if Recommendation AI fails, the deterministic order is
shown and is explicitly labelled as a verified fallback.

The time-value baseline is regressed from what the member chose the last time they
had a cost/time trade-off in front of them — and so are their tolerance for churn
and their tolerance for a plan that might fail on the day.

Once the *whole trip* is priced, the same-day rebooking turns out to be the right
answer for all three synthetic histories. That is the finding, not a limitation:
the cheap two-day fare only looks cheap until the forfeited nights, the abandoned
park day and the extra fragility are counted, and the point of pricing the whole
journey is that it stops the fare deciding on its own.

The history still does real work. It shows in where the cheap option lands:

| Inferred history | Weighting | Rank of the cheapest fare (−SGD 495, 46h) |
| --- | --- | ---: |
| Time-sensitive | SGD 45/hr, SGD 58.50/booking, 25% risk tolerance | 8th of 9 |
| Balanced | SGD 25/hr, SGD 32.50/booking, 50% risk tolerance | 7th of 9 |
| Cost-sensitive | SGD 8/hr, SGD 15/booking, 75% risk tolerance | 3rd of 9 |

When the member states an objective outright, the answers separate completely:

| Objective | Recommendation |
| --- | --- |
| Lowest cost | **NH860 · cheapest** — −SGD 495, two nights written off |
| Earliest arrival | **NH802 · cheapest** — arrives 19:35, 2h 30m lost |
| Least disruption | **SQ12 · fastest** — nothing given up, three bookings touched |

The selector in the UI is an inspection view for reviewers. A given member only
ever has one active.

## Five objectives, not one

| Objective | What it counts |
| --- | --- |
| **Money** | Whole-trip impact, signed against the confirmed trip |
| **Time** | Hours of the trip given up |
| **Disruption** | Bookings that have to be re-transacted |
| **Experience** | Value destroyed by giving up something bought for its own sake |
| **Fragility** | Chance the plan fails on the day, compounded across its legs |

Every one of them is a Pareto axis, so a plan only reaches the front if nothing
else beats it on all five at once. The last two are the ones that stop the optimizer cheating. Refunding the park
passport returns the money but not the day, so `experience_lost` prices the loss
at 1.5× what was paid — a purchase reveals willingness-to-pay *at or above* the
price, so valuing it at exactly the price makes deleting the trip read as free.
And fragility compounds: a direct flight followed by the last train of the night
is not a low-risk plan just because the flight is. Released bookings stay in that
product, because giving up a reserved transfer does not make the evening more
reliable — it puts the member on an unmanaged fallback.

## Connectors, MCP and AI agents

TripShield has two deliberately separate integration surfaces:

- Travel inventory and status use direct REST connector adapters. Each read reports
  `live`, `sandbox`, or `fixture` provenance and falls back to complete fixture inventory
  when credentials, availability, currency, or an upstream response are unusable.
- Claude and GPT-5.6 Sol use embedded, request-bound **TripShield MCP** snapshots.
  Each agent receives a role-specific read-only tool allowlist. Specialists can read
  their own recovery tasks, cached validated inventory and member-choice context; the
  Recommendation AI can read the graph, candidate plans, history and specialist findings.
  Models receive no write, booking, cancellation, payment, arbitrary HTTP or scraping tool.

| Adapter | Read path | Transaction path |
| --- | --- | --- |
| [AeroDataBox](https://doc.aerodatabox.com/) | Production flight status with a key; otherwise recorded fixture | Fixture only |
| [Duffel](https://duffel.com/docs/api/offer-requests) | Test-mode offer requests; otherwise fixture offers | Fixture only |
| [LiteAPI](https://docs.liteapi.travel/reference/overview) | Sandbox hotel rates; otherwise fixture rates | Fixture only |
| Activities | Fixture inventory | Fixture only |
| Dining | Fixture inventory | Fixture only |
| Ground | Fixture inventory | Fixture only |

Viator documents an official [Experiences MCP](https://docs.viator.com/partner-api/mcp/),
but access and integration are deferred. TableCheck and JR East do not provide a stable,
self-serve booking API selected for this build. TripShield does not scrape those sites—or
any supplier site—at runtime.

There is no FX feed in this release, so non-SGD inventory is rejected instead of being
converted with a guessed rate. No Amex member-account, Card transaction, entitlement,
payment, or booking API is connected; those records remain synthetic demonstration data.

### Synthetic dummy data used for missing sources

The app is intentionally complete without external credentials. Whenever a source is
unavailable, it uses deterministic **synthetic dummy data** rather than scraping a site,
guessing a value, or returning a partly populated plan. Fixture options are explicitly
returned with `source_mode: "fixture"` and `synthetic: true`; authenticated API results
use `synthetic: false`. `/api/connectors` also publishes the fallback type for every
adapter.

| Missing or unavailable source | Synthetic dummy data included |
| --- | --- |
| Amex member and Card APIs | One fictional member, demo credentials, balances, four benefits and six statement transactions |
| Booking-history API | Seven fictional bookings with references, prices, refund rules and dependency edges |
| AeroDataBox credential or outage | Two recorded-format flight-status fixtures, including the SQ638 cancellation |
| Duffel credential, outage or unusable offers | Six deterministic flight options with timings, SGD prices, risk and change rules |
| LiteAPI credential, outage or unusable rates | Four deterministic lodging options with dates, SGD prices and cancellation rules |
| Viator approval | Four deterministic activity options, including re-date and refund alternatives |
| TableCheck public API | Four deterministic dining alternatives |
| JR East/ground booking API | Five deterministic transfer alternatives |
| Member-choice history | Three fictional preference histories used by the deterministic ranker |
| Supplier transactions and refunds | Fixture receipts, authorisations, cancellation quotes and compensation results; nothing is charged or cancelled |

These fixtures are demo inputs, not cached claims about current availability. Real
carrier, hotel and attraction names are used only to make the scenario understandable;
the availability, price, member relationship, booking reference and transaction outcome
are invented.

Amex program labels are conservative. A supplier receives an Amex badge only after an
exact name or explicit-alias match against the curated catalog in
`api/tripshield/amex_partners.py`. The catalog is maintained from official Amex pages,
stores its verification date, and is never refreshed by runtime scraping.

Configure any subset of these environment variables; the app remains fully usable
without them:

```bash
AERODATABOX_API_KEY=your_rapidapi_key
DUFFEL_ACCESS_TOKEN=duffel_test_token
LITEAPI_SANDBOX_KEY=liteapi_sandbox_key
ANTHROPIC_API_KEY=anthropic_key
OPENAI_API_KEY=openai_key
AI_PROVIDER=anthropic              # or openai; optional
ANTHROPIC_MODEL=claude-sonnet-5    # optional
OPENAI_MODEL=gpt-5.6-sol           # optional
AI_TIMEOUT_SECONDS=8               # optional
```

When `AI_PROVIDER` is unset, Anthropic is preferred when configured, then OpenAI. An
explicitly selected provider never silently fails over to the other provider. Model
errors or invalid output are discarded and the deterministic recommendation remains.

## Cancel means two different things

The execution engine separates them, because conflating them is how a member ends up
with a half-recovered trip:

- **Stop here** — execute nothing further. Committed steps stay committed.
- **Undo everything** — run compensating transactions in reverse order.

The second is quoted before it runs. An airline returns the fare net of its fee; a
same-day restaurant cancellation returns nothing; and a step that *gave a booking up*
cannot be reversed by a refund at all — only by buying it back at today's price, if the
inventory still exists. The dialog shows the unrecoverable column before the member
decides, not after.

## Getting started

```bash
npm install
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Python **3.10 or newer** is required by MCP v2; Python 3.12 is recommended. On Windows,
use `.venv\Scripts\python` in place of `.venv/bin/python`.

Two processes, in separate terminals:

```bash
npm run dev:api
```

```bash
npm run dev
```

The frontend is at `http://localhost:5173` and proxies `/api` to port 8000. Interactive
API docs are at `http://localhost:8000/api/docs`.

If port 8000 is taken, run the backend elsewhere and tell Vite where it went — the proxy
target reads `API_PORT`:

```bash
uvicorn api.index:app --reload --port 8010
```

```bash
API_PORT=8010 npm run dev
```

Demo credentials: `demo@amextravel.com` / `Travel123!`

## Layout

```
api/index.py                  HTTP surface — routes and request validation only
api/tripshield/
  domain.py                   Types shared by everything below
  catalog.py                  The demonstration booking history
  graph.py                    Dependency graph, impact propagation, node splicing
  connectors.py               Direct REST adapters and deterministic fallbacks
  amex_partners.py            Curated, officially verified Amex partner matches
  mcp_server.py               Embedded request-bound, role-scoped read-only MCP
  ai.py                       Provider-neutral structured model runtime
  ai_agents.py                Five specialist agents and Recommendation AI
  agents.py                   Feasibility, connector reads and deterministic fallbacks
  optimizer.py                Metrics, eligibility, fallback and AI-order validation
  orchestrator.py             The workflow: detect → plan → validate → approve
  execution.py                Saga engine: sequential commit, compensation
  store.py                    Session state

web/src/main.js               Bootstrap: login, view switching
web/src/api.js                Typed fetch client
web/src/views/
  account.js                  The calm state, before anything goes wrong
  recovery.js                 The five-stage console shell
  detect.js                   Stage 1 — the flight sweep and the connector table
  graph.js                    Stage 2 — the dependency graph and impact table
  trace.js                    Stage 3 — tasks, agents, and what they ruled out
  plans.js                    Stage 3 — candidate plans and the weighting controls
  editor.js                   Stage 4 — drag-and-drop, revalidated server-side
  execute.js                  Stage 5 — progress, authorisation, rollback
web/src/styles/               tokens.css (DESIGN.md verbatim) · base · app · console

legacy/                       The two original standalone prototypes
DESIGN.md                     The American Express design system this is built on
```

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness |
| `POST` | `/api/auth/login` | Demo credential check |
| `GET` | `/api/account` | Member, card, transactions, benefits, trip |
| `GET` | `/api/bookings` | The single source of truth — every booking on one Card |
| `GET` | `/api/connectors` | Adapter read/transaction modes plus AI/MCP status |
| `GET` | `/api/connectors/health` | Bounded, sanitized read-only upstream checks |
| `GET` | `/api/flights/status` | Passthrough to the flight-status connector |
| `POST` | `/api/disruption/detect` | Sweep every upcoming flight |
| `GET` | `/api/graph` | The graph plus each node's fate under the disruption |
| `POST` | `/api/recovery/plan` | Create tasks, delegate, assemble and rank plans |
| `GET` | `/api/recovery/rank` | Re-score existing plans and rerun only Recommendation AI |
| `POST` | `/api/recovery/plan/validate` | Re-check an arbitrary, possibly hand-edited plan |
| `POST` | `/api/recovery/plan/approve` | Freeze a snapshot, queue the transactions |
| `GET` | `/api/execution/{id}` | Run state |
| `POST` | `/api/execution/{id}/advance` | Execute exactly one transition |
| `GET` | `/api/execution/{id}/rollback-quote` | What undoing would really return. Read-only |
| `POST` | `/api/execution/{id}/cancel` | Stop, or stop and compensate |

## Deployment

Vercel builds `web/` to `dist/` and serves `api/index.py` as a Python serverless
function; `vercel.json` rewrites `/api/*` onto it.

```bash
npx vercel --prod
```

One caveat: execution runs live in process memory. Across multiple warm serverless
instances a run can become unreachable, and the API returns `410` rather than inventing
one — runs record transactions that actually happened and must never be reconstructed
from seed data. Production would put them in Postgres and key compensation off the
transaction log; the interface in `store.py` is already that shape.

## Verification

The default suite is offline and uses mocked upstream/model responses:

```bash
.venv/bin/python -m pytest
npm run build
```

Credentialed read-path smoke tests are opt-in and never book anything:

```bash
.venv/bin/python -m pytest -m live
```

Each live check skips itself when its corresponding AeroDataBox, Duffel, or LiteAPI
credential is absent.

## Provenance

Booking references, member data and all executed transactions are synthetic. Search
options may come from authenticated production/sandbox read paths and carry explicit
provenance; fallback options remain fixtures. Amex partner claims come only from the
reviewed official-source catalog, never from fuzzy matching or runtime scraping.

## Licence

MIT — see [LICENSE](LICENSE). American Express is a trademark of American Express
Company. Design tokens in `DESIGN.md` are sourced from
[designmd.co](https://www.designmd.co/d/american-express).
