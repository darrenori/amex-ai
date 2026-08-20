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
| 5 | **Delegate** | Five specialized agents fan out concurrently over their MCP connectors. |
| 6 | **Assemble** | Options are combined into *whole* candidate plans, one per strategy per flight. |
| 7 | **Optimize** | Pareto front, then a scalarised score under a chosen or inferred weighting. |
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
passport looks cheaper, faster and less disruptive than one that re-dates it. So
`experience_lost` is a fourth objective on the Pareto front, priced at what the member
paid on the revealed-preference argument that they valued it at least that much. Without
it, the optimizer wins by quietly deleting the trip.

## Personalization

The time-value weight is never a setting the member picks. It is regressed from what they
actually chose the last time they had a cost/time trade-off in front of them — and so is
their tolerance for churn, because a member who waits two days for a fare drop is also a
member who does not mind their hotel being rebooked.

The same cancellation, the same seven bookings, three different histories:

| Inferred history | Weighting | Recommendation |
| --- | --- | --- |
| Time-sensitive | SGD 45/hr, SGD 58.50/booking | **SQ12 · fastest** — +SGD 188, nothing given up |
| Balanced | SGD 25/hr, SGD 32.50/booking | **SQ12 · cheapest** — +SGD 55, park passport refunded |
| Cost-sensitive | SGD 8/hr, SGD 15/booking | **NH860 · cheapest** — −SGD 495, two nights written off |

The selector in the UI is an inspection view for reviewers. A given member only ever has
one active.

## Real APIs

Every connector is declared against a real, currently-available product, and every tool
name maps to a real endpoint on it.

| Connector | Upstream | Mode | Key endpoints |
| --- | --- | --- | --- |
| `status` | [AeroDataBox](https://doc.aerodatabox.com/) | **live** with a key | `GET /flights/number/{number}/{date}` |
| `flights` | [Duffel](https://duffel.com/docs/api/order-cancellations) | fixture | `POST /air/order_change_requests`, `POST /air/order_cancellations` |
| `lodging` | [LiteAPI (Nuitée)](https://docs.liteapi.travel/reference/overview) | fixture | `POST /rates/prebook`, `POST /rates/book`, `PUT /bookings/{id}/cancel` |
| `activities` | [Viator Partner API](https://docs.viator.com/partner-api/technical/) | fixture | `POST /bookings/cancel-quote`, `POST /bookings/cancel` |
| `dining` | TableCheck | fixture | `PATCH /reservations/{id}` |
| `ground` | JR East | fixture | `POST /reservations` |

Detection runs **live** against AeroDataBox as soon as `AERODATABOX_API_KEY` is set,
because reading a flight's status is free and read-only:

```bash
AERODATABOX_API_KEY=your_rapidapi_key npm run dev:api
```

Without it, the sweep replays a recorded response in the same shape. The status
vocabulary is AeroDataBox's own — `Expected`, `EnRoute`, `Boarding`, `Departed`,
`Delayed`, `Arrived`, `Canceled`, `Diverted`, `CanceledUncertain`.

Booking transactions stay on fixtures deliberately: a demonstration must not transact
against live inventory. Duffel's two-phase cancellation (quote first, confirm second) and
Viator's `cancel-quote` / `cancel` split are modelled faithfully, because they are exactly
why rollback cannot be treated as "just undo it".

> **Note on Amadeus.** The obvious choice for this stack used to be the Amadeus
> Self-Service APIs. Amadeus [decommissioned that portal on 17 July 2026](https://www.phocuswire.com/amadeus-shut-down-self-service-apis-portal-developers);
> keys no longer return data. Duffel and LiteAPI are the current alternatives with real
> free sandboxes.

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
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
```

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
  connectors.py               MCP-style clients onto the real travel APIs
  agents.py                   Five specialized recovery subagents
  optimizer.py                Pareto front and scalarised ranking
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
| `GET` | `/api/connectors` | Which MCP servers exist and whether each is live |
| `GET` | `/api/flights/status` | Passthrough to the flight-status connector |
| `POST` | `/api/disruption/detect` | Sweep every upcoming flight |
| `GET` | `/api/graph` | The graph plus each node's fate under the disruption |
| `POST` | `/api/recovery/plan` | Create tasks, delegate, assemble and rank plans |
| `GET` | `/api/recovery/rank` | Re-score existing plans under a different weighting |
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

## Provenance

Every price, seat, reference, member and availability figure is synthetic. The carriers,
routes, properties and attractions are real and really do connect the way they are
modelled. The API endpoint paths, status vocabularies and two-phase cancellation flows
are taken from the live products named above.

## Licence

MIT — see [LICENSE](LICENSE). American Express is a trademark of American Express
Company. Design tokens in `DESIGN.md` are sourced from
[designmd.co](https://www.designmd.co/d/american-express).
