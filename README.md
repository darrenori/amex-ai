# TripShield

A **Travel Recovery Orchestrator** for American Express Card Members. An independent
concept prepared for the **AMEX AI Hackathon 2026** — not an official American Express
product, and not affiliated with or endorsed by American Express.

The member UI presents the three highest-ranked complete recovery plans. Replacement
flight, hotel, dining and ground-service titles link to the relevant approved American
Express service in a new tab. Activity titles remain plain text when no appropriate
approved destination is available; arbitrary supplier URLs are never rendered.

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

Singapore → Tokyo → Osaka. Seven bookings, one Card.

The itinerary is anchored to whenever you run it, always a few weeks ahead, so the
demo cannot rot into a trip that has already happened. Dates below are illustrative.

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

## System architecture

Three processes, and one rule about which of them is allowed to decide anything.

```
  Browser                      Render (FastAPI)                 Upstreams
  ┌────────────────┐           ┌──────────────────────┐         ┌──────────────┐
  │ vanilla JS     │           │ index.py   routes    │  REST   │ AeroDataBox  │
  │  + React       │  HTTPS    ├──────────────────────┤ ──────► │ Duffel       │
  │  islands       │ ────────► │ orchestrator         │         │ LiteAPI      │
  │                │           │  ├ graph.py          │         └──────────────┘
  │ proposes only  │ ◄──────── │  ├ agents.py         │
  └────────────────┘   JSON    │  ├ optimizer.py      │  in-process MCP
        Vercel                 │  └ execution.py      │ ──────► ┌──────────────┐
                               │ ai.py / ai_agents.py │         │ model agents │
                               └──────────────────────┘ ◄────── └──────────────┘
                                                          strict JSON
```

The frontend is a vanilla-JS shell with a few React "islands" mounted into it: the
dependency graph, the journey map, the rewards chart. It renders, it proposes, and it
decides nothing. Every hand edit goes back to the server to be revalidated.

The backend is deliberately **stateful**. Sessions and execution runs live in process,
because a run records transactions that actually happened and cannot be rebuilt from
seed data. That is why it runs as a long-lived service rather than as serverless
functions, and why a missing run returns `410` instead of a freshly seeded one.

The model layer sits to the side of that spine, not inside it. Nothing on the critical
path waits on a model to be correct, or available.

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

## The AI layer

Six bounded agents, none of which can change a fact.

| Agent | Reads | Decides |
| --- | --- | --- |
| Flight AI | Its own tasks, validated flight inventory, member history | Preference order of already-feasible flights |
| Accommodation AI | Same, for lodging | Preference order, and which nights are worth forfeiting |
| Activity AI | Same, for attractions | Preference order under date rules |
| Dining AI | Same, for reservations | Preference order under deposit rules |
| Ground AI | Same, for transfers | Preference order under buffer rules |
| Recommendation AI | Trip graph, validated plans, specialist findings, history | Order of **eligible** plans, and the sentence the member reads |

What none of them can do is more interesting than what they can. Feasibility is settled
before a model is asked anything: a park passport whose date has passed is not a
judgement call, so `agents.py` rules it out deterministically and the model never sees
it. The model orders what survived. `optimizer.py` then checks the order it returned
against the eligible set, and discards it whole if it does not match.

**Every agent is fail-closed.** If a model is unavailable, out of credit, slow, or
returns something that breaks its contract, the deterministic specialist runs instead
and the plan is unaffected. The trace says which happened and why, naming the actual
cause — a missing key, an exhausted balance, a broken output contract and a truncated
answer are four different problems and read as four different sentences.

### How a model is bounded

Each agent gets a **request-bound MCP snapshot** with a role-scoped, read-only tool
allowlist. A specialist can read its own tasks, its own cached inventory, and member
history. It cannot see another specialty's tasks, and there is no tool for booking,
cancelling, paying, fetching a URL, or writing anything at all. The snapshot is
immutable and dies with the request.

Provider quirks are handled at the adapter boundary rather than leaking into the MCP
layer. Two are worth knowing about, because both fail *before* a call is billed and so
look like outages rather than bugs:

- Strict structured output rejects `uniqueItems`, and strict function calling requires
  every object to set `additionalProperties: false` and to name every property in
  `required`. When the MCP SDK is installed, FastMCP derives tool schemas from the
  Python signature and emits neither. Schemas are normalised in `ai.py` on the way out.
- Agents run concurrently, so a provider error arrives wrapped in an `ExceptionGroup`.
  Classifying the wrapper reports every failure as a generic provider error and hides
  the cause, so it is unwrapped before it is classified.

### What a run costs

The snapshot is a projection, not the full record. An agent ranking options needs what
tells two options apart, not the plumbing that books them, so adapter calls, offer ids,
connector keys and provenance stay server-side:

```
  before   110,562 chars   ~27,600 input tokens
  after     55,537 chars   ~13,900 input tokens
```

| Knob | Value | Why |
| --- | --- | --- |
| Default model | `gpt-5.4-mini` | Small bounded work; roughly a second per call against the flagship's several |
| Tool rounds | 3 | An agent reads its tools in one round and answers in the next |
| Output ceiling | 900 floor, scaled per agent | Eight lodging tasks cannot be enumerated in the same breath as one flight |
| Validation retries | 1 | Model slips on exact permutations are not deterministic; one retry converts most |
| Timeout | 45s | A bounded agent measures 10–23s; a default the work does not fit inside times out everything |
| SDK retries | 0 | A 429 for an exhausted balance cannot succeed on retry |

### Providers

`AI_PROVIDER` selects OpenAI or Anthropic. OpenAI has no free tier, but its wire
protocol is spoken by providers that do, so `OPENAI_BASE_URL` redirects the same client
at Gemini, Groq, OpenRouter or a local Ollama with tool calling and strict structured
output untouched. `.env.example` carries the URLs. With nothing configured at all the
app still returns every plan, deterministically ranked.

## Connectors, MCP and AI agents

TripShield has two deliberately separate integration surfaces:

- Travel inventory and status use direct REST connector adapters. Each read reports
  `live`, `sandbox`, or `fixture` provenance and falls back to complete fixture inventory
  when credentials, availability, currency, or an upstream response are unusable.
- Model agents use embedded, request-bound **TripShield MCP** snapshots.
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
self-serve booking API selected for this build. TripShield does not scrape those sites, or
any supplier site, at runtime.

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

Configure any subset of the names listed in `.env.example`; the app remains fully usable
without them. Real values belong only in an ignored local `.env*` file, the shell process
environment, or the hosting provider's encrypted environment settings:

```bash
AERODATABOX_API_KEY=<set-in-environment>
DUFFEL_ACCESS_TOKEN=<set-in-environment>
LITEAPI_SANDBOX_KEY=<set-in-environment>
LITEAPI_HOTEL_IDS=<set-in-environment>
ANTHROPIC_API_KEY=<set-in-environment>
openai_api_key=<set-in-environment>      # same secret name on Vercel and Render
AI_PROVIDER=openai                       # optional; selects OpenAI explicitly
ANTHROPIC_MODEL=claude-sonnet-5          # optional
OPENAI_MODEL=gpt-5.4-mini                # optional; the cheap tier is the default
OPENAI_BASE_URL=                         # optional; any OpenAI-compatible endpoint
AI_TIMEOUT_SECONDS=45                    # optional; a bounded agent takes 10-23s
```

For local development, copy `.env.example` to the ignored `.env.local`, add values there,
and start the backend with `npm run dev:api:env`. Vercel values belong in the project's
encrypted Environment Variables settings instead of any committed file.

The deployed configuration uses `AI_PROVIDER=openai` and the `openai_api_key` secret
on Render, which is where the backend runs. The conventional `OPENAI_API_KEY` spelling remains accepted
for local development. When `AI_PROVIDER` is unset, Anthropic is preferred when
configured, then OpenAI. An explicitly selected provider never silently fails over to
the other provider. Model errors or invalid output are discarded and the deterministic
recommendation remains.

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
web/src/api.js                Typed fetch client; defaults to the Render service
web/src/views/
  account.js                  The calm state, before anything goes wrong
  recovery.js                 The five-stage console shell
  detect.js                   Stage 1 · the flight sweep and the connector table
  graph.js                    Stage 2 · the dependency graph and impact table
  trace.js                    Stage 3 · tasks, agents, and what they ruled out
  plans.js                    Stage 3 · candidate plans and the weighting controls
  editor.js                   Stage 4 · drag-and-drop, revalidated server-side
  execute.js                  Stage 5 · progress, authorisation, rollback
  partners.js                 The render-boundary link allowlist
  tutorial.js                 The walkthrough, opened once then kept in the app bar
web/src/islands/              React mounted into the vanilla shell
  mount.js                    Mount and teardown, keyed by container node
  DependencyGraph.jsx         Draggable graph; click a booking to trace its chain
  ResolveChoropleth.jsx       Journey map; dotted legs that resolve as the run commits
  AreaTrendChart.jsx          Rewards trend on the account overview
web/src/styles/               tokens.css (DESIGN.md verbatim) · base · app · console · islands

.github/workflows/ci.yml      Backend, frontend and the tracked-secret gate
.github/workflows/keepalive.yml  Keeps the free backend from sleeping

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

The frontend and the backend are deployed separately, and that split is not incidental.

| Piece | Host | Why there |
| --- | --- | --- |
| `web/` → `dist/` | Vercel (static) | A built SPA; nothing to keep warm |
| `api/` | Render (web service) | Stateful and long-running |

The backend cannot be serverless. Sessions and execution runs live in process, so a
second instance does not have them: approving a plan would land somewhere that has never
heard of it and return `410`, which is the honest answer but not a working product. One
plan request also runs six agents for a good part of a minute, past a typical function
duration limit. `store.py` says all of this at the top; production would move runs into
Postgres and key compensation off the transaction log, and the interface is already that
shape.

A production build therefore defaults to the Render service rather than to same-origin
`/api`, so a deployment cannot silently talk to a serverless copy of a stateful API.
`VITE_API_BASE` overrides it for a self-hosted backend, and development still proxies to
the local uvicorn.

```bash
VITE_API_BASE=https://your-api.onrender.com/api   # optional; the default is the Render service
```

Set on the backend: `AI_PROVIDER`, an API key, optionally `OPENAI_MODEL`,
`OPENAI_BASE_URL` and `AI_TIMEOUT_SECONDS`. See `.env.example`; nothing is required for
the app to work.

Render's free tier sleeps after about fifteen minutes and takes roughly fifty seconds to
wake, which is long enough that the first request of a demo times out. A scheduled
workflow in `.github/workflows/keepalive.yml` pings `/api/health` every ten minutes to
keep it resident. Note that a free service is metered at 750 hours a month and staying
awake all month spends about 744 of them.

## Verification

The default suite is offline and uses mocked upstream/model responses:

```bash
.venv/bin/python -m pytest
npm run test:web
npm run build
```

GitHub Actions runs the backend suite on Python 3.12 and the Vercel-compatible Python
3.14 runtime, runs the frontend suite/build on Node 22, checks dependency consistency,
and rejects tracked `.env*` files or credential-shaped values. CI requires no secrets;
credentialed upstream smoke tests remain explicitly opt-in and read-only.

The browser-facing integration tests verify that only the three highest-ranked complete
plans are rendered, external replacement links open safely, arbitrary supplier URLs are
rejected, and activities without an appropriate approved destination remain plain text.

| Integration | Default automated coverage | Optional live coverage |
| --- | --- | --- |
| AeroDataBox | Injected response normalization, cancellation detection, sanitized fallback and health response | Read-only production status smoke test |
| Duffel | Test-offer request/response contract, SGD filtering, caching, fallback completeness and simulated execution | Read-only test-mode offer search |
| LiteAPI | Sandbox request/response contract, SGD filtering, Amex partner matching and fallback completeness | Read-only sandbox rate search |
| Activities, dining and ground | Complete synthetic inventories, provenance, ownership and health responses | None; no approved live adapter is configured |
| OpenAI and Anthropic | Provider selection, role-scoped MCP discovery, tool calls, strict output validation and fail-closed fallback | None; model calls are never required for the default suite |
| TripShield MCP | Immutable snapshots, role tool allowlists and in-process MCP v2 calls | Not applicable |
| API and execution | Detection → planning → validation → approval → gated execution → rollback/audit | Not applicable; transactions remain simulated |
| Member UI | Three-plan limit, approved new-tab destinations, unsafe-link rejection and production build | Browser walkthrough against the local API |

Credentialed read-path smoke tests are opt-in and never book anything:

```bash
.venv/bin/python -m pytest -m live
```

Each live check skips itself when its corresponding AeroDataBox, Duffel, or LiteAPI
credential is absent.

## Outbound links

A link is the one thing on the screen that can take a Card Member somewhere else
entirely, and these arrive from the API as data. Data is not trusted because it came
from our own backend, so the allowlist is enforced at the render boundary in
`partners.js`, where it holds no matter which connector or agent produced the link.

Two Amex hosts serve this market: the global site, and the Singapore booking application
at `travel.americanexpress.com.sg`. The second is **not** a subdomain of the first, so it
is named explicitly rather than assumed. Each entry matches only itself or its own
subdomains, so `evil-americanexpress.com.sg` and `americanexpress.com.sg.attacker.io`
are both rejected.

Links are derived from the option rather than written by hand, so the label names the
supplier and the date the member is being sent to book, and live inventory gets the same
treatment as a fixture. They point at the booking application rather than the page
describing it. They do not point deeper than that: a real Amex Travel result URL carries
a search id the platform mints when a search runs against it, and that is not derivable
from an origin, a date and a cabin.

No property URL is invented. `amex_partners.py` requires a human to verify a record
before a supplier gets a link of its own, and a test pins every shipped destination to an
allowlist of URLs that were actually requested and answered — a plausible-looking guess
that 404s is worse than a page one level up.

## Provenance

Booking references, member data and all executed transactions are synthetic. Search
options may come from authenticated production/sandbox read paths and carry explicit
provenance; fallback options remain fixtures. Amex partner claims come only from the
reviewed official-source catalog, never from fuzzy matching or runtime scraping.

## Licence

MIT — see [LICENSE](LICENSE). American Express is a trademark of American Express
Company. Design tokens in `DESIGN.md` are sourced from
[designmd.co](https://www.designmd.co/d/american-express).
