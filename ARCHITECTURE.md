# TripShield — AI Architecture

How a flight cancellation becomes a re-arranged trip, and where in that pipeline
a model is actually doing the work.

The short version: **the model does not decide the plan.** Whether a member can
still reach a timed park entry is arithmetic, and arithmetic can be tested. The
graph, feasibility rules, Pareto front, scores, ordering, reason codes and
execution saga remain deterministic. Claude or GPT-5.6 Sol may read an immutable
snapshot through one embedded MCP server and explain the recommendation.

---

## 1. The whole system

```mermaid
flowchart TB
    subgraph HUMAN["Human-in-the-loop · web/"]
        UI["Recovery console<br/>5 staged views"]
        EDIT["Plan editor<br/>drag / keyboard"]
        APPROVE["Approval + per-charge<br/>authorisation"]
    end

    subgraph ORCH["Travel Recovery Orchestrator · api/tripshield/"]
        DETECT["1 · Detect"]
        GRAPH["2 · Reconstruct + propagate<br/>graph.py"]
        TASKS["3 · Create recovery tasks<br/>orchestrator.py"]
        AGENTS["4 · Delegate to subagents<br/>agents.py"]
        ASSEMBLE["5 · Assemble whole plans<br/>orchestrator.py"]
        RANK["6 · Pareto + scalarised rank<br/>optimizer.py"]
        VALIDATE["7 · Validate any plan<br/>orchestrator.materialize"]
        EXEC["8 · Execute + compensate<br/>execution.py"]
        AUDIT["Reason codes + audit<br/>explain.py"]
    end

    subgraph REST["Direct REST adapters · connectors.py"]
        C1["AeroDataBox<br/>production status read"]
        C2["Duffel<br/>test offer search"]
        C3["LiteAPI<br/>sandbox hotel search"]
        CF["Activities · dining · ground<br/>fixture inventory"]
    end

    subgraph CONTEXT["Read-only model context"]
        MCPS["TripShield Context MCP<br/>embedded · request-bound"]
        MODEL["Claude or GPT-5.6 Sol<br/>explanation only"]
    end

    UI --> DETECT
    DETECT --> GRAPH --> TASKS --> AGENTS --> ASSEMBLE --> RANK --> UI
    UI --> EDIT --> VALIDATE --> UI
    EDIT --> APPROVE --> EXEC --> UI
    APPROVE --> AUDIT
    RANK --> AUDIT

    DETECT -.->|poll / webhook| C1
    AGENTS -.->|read-only search| C2 & C3 & CF
    EXEC -.->|simulated transaction| CF
    GRAPH & RANK -.->|immutable snapshot| MCPS --> MODEL
    MODEL -.->|validated prose| UI
```

MCP is not the connector transport. AeroDataBox, Duffel and LiteAPI are ordinary
HTTP clients with explicit deadlines and deterministic fixture fallbacks. Every
booking, change, cancellation, refund and payment remains simulated even when a
candidate came from a production or sandbox read.

---

## 2. Detection — event-driven, not user-reported

The member never tells the system anything. A scheduled sweep and the carrier's
alert webhook both land on the same code path, which is also what the *Check my
flights* button calls.

```mermaid
sequenceDiagram
    autonumber
    participant Cron as Scheduled sweep
    participant Det as orchestrator.detect
    participant ADB as AeroDataBox
    participant Store as Session

    Cron->>Det: every upcoming flight on the trip
    loop each flight booking
        Det->>ADB: GET /flights/number/{number}/{date}
        ADB-->>Det: status ∈ {Expected, Delayed, Canceled, Diverted, …}
    end
    Det->>Det: status ∈ DISRUPTIVE_STATUSES ?
    Det->>Store: cancelled = [bk_flight_out]
    Det-->>Cron: disruption detected, recovery begins
```

`Canceled` (one *l*), `CanceledUncertain` and `Diverted` are AeroDataBox's own
vocabulary, used verbatim rather than re-mapped. This connector runs **live**
against the real API when `AERODATABOX_API_KEY` is set. Without a usable response,
the same detection path returns a recorded fixture with fallback provenance.

---

## 3. The dependency graph — where the reasoning is deterministic

The trip is a DAG, not a list. Two bookings can be adjacent in time and unrelated;
two can be days apart and one cannot happen without the other.

```mermaid
flowchart TB
    F["✈ SQ638 SIN→NRT<br/>18 Sep 09:00 → 17:05"]:::dead
    T["🚄 Narita Express<br/>18:15 → 19:20"]
    H["🏨 Hilton Tokyo Bay<br/>check-in 18 Sep"]
    D["🍣 Sushi counter<br/>18 Sep 20:00"]
    A["🎟 Tokyo Disneyland<br/>19 Sep 09:00"]
    G["✈ GK205 NRT→KIX<br/>21 Sep 14:00"]
    O["🏨 Hotel Granvia Osaka<br/>check-in 21 Sep"]

    F -->|"HARD · 1h<br/>immigration + baggage"| T
    T -->|"HARD · 45m<br/>how you reach the property"| H
    F -.->|"SOFT · 2h30<br/>a deposit, not the trip"| D
    F -->|"HARD · 10h<br/>dated passport"| A
    H -.->|"SOFT · 0m<br/>shuttle from the property"| A
    A -->|"HARD · 3h<br/>LCC check-in cut-off"| G
    G -->|"HARD · 1h30<br/>desk cut-off"| O

    classDef dead fill:#FAE9E8,stroke:#C52720,stroke-width:2px
```

Propagation is one pass in topological order, and the rule is arithmetic:

```
earliest_reachable = free_at(upstream) + edge.min_buffer

if earliest_reachable > deadline(node):
    HARD edge  → BROKEN
    SOFT edge  → AT_RISK
```

Three refinements that took real bugs to find:

| Rule | Why it exists |
| --- | --- |
| `free_at` is a stay's **check-in**, not its check-out | Otherwise a 3-night booking looks like it blocks the whole trip |
| A **replacement is still checked** against its own edges | Otherwise a hotel option can assert a check-in the member cannot make |
| A **dropped** booking is spliced out, not cascaded | Giving up the transfer does not mean giving up the hotel — you still have to get there |

```mermaid
flowchart LR
    subgraph BEFORE["Cancel the transfer"]
        A1["✈ Flight"] -->|HARD 1h| B1["🚄 Transfer"] -->|HARD 45m| C1["🏨 Hotel"]
    end
    subgraph AFTER["Graph is spliced, not broken"]
        A2["✈ Flight"] -->|"HARD 1h45<br/>buffers summed"| C2["🏨 Hotel"]
    end
    BEFORE ==> AFTER
```

---

## 4. Task creation and agent fan-out

The orchestrator creates tasks **dynamically**, per candidate flight — because a
different arrival breaks a different set of things.

```mermaid
flowchart TB
    ROOT["Flight Agent<br/>task_bk_flight_out"]
    ROOT --> O1["SQ12 · same-day 21:50"]
    ROOT --> O2["NH802 · same-day 19:35"]
    ROOT --> O3["CX715 · next morning"]
    ROOT --> O4["NH860 · in two days"]

    O1 --> P1["re-propagate →<br/>3 bookings affected"]
    O3 --> P3["re-propagate →<br/>6 bookings affected"]

    P1 --> W1["Accommodation · Activity<br/>Dining · Ground agents"]
    P3 --> W3["Accommodation · Activity<br/>Dining · Ground agents"]

    W1 --> C1["candidate options"]
    W3 --> C3["candidate options"]
```

Each task carries the constraint its **own edge** demands, not a constant:

```json
{
  "id": "task_bk_activity_tdl_ft_cx715",
  "agent": "Activity Agent",
  "objective": "Restore “Attraction” given the replacement arrival",
  "constraints": {
    "arrival": "2026-09-19T19:40:00+09:00",
    "arrival_buffer_minutes": 600,
    "must_end_before": "2026-09-21T11:00:00+09:00",
    "priority": "inferred"
  },
  "tools": ["check_availability", "book", "quote_cancellation", "cancel"]
}
```

Agents return **several** options with a reason for every rejection — the
rejections are the part that makes the recommendation believable:

```
flights.search_offers → 4 candidates
  ✓ SQ12 · same-day evening direct        +SGD 180
  ✓ NH802 · earlier same-day direct       +SGD 320
  ✗ ruled out opt_lod_transit: the replacement leaves the same day —
    there is no night to cover
```

---

## 5. Plan assembly — whole answers, not a shortlist

The failure mode this avoids: asking each agent for its single best option and
stapling the results together produces a plan nobody chose, whose totals describe
no trip the member could actually take.

```mermaid
flowchart LR
    subgraph WRONG["✗ Stapled"]
        direction TB
        X1["cheapest flight"] --- X2["least-disruptive hotel"] --- X3["fastest transfer"]
        X4["totals describe no real trip"]
    end
    subgraph RIGHT["✓ Assembled per strategy"]
        direction TB
        Y1["for each candidate flight"] --> Y2["for each strategy<br/>cost · time · disruption · balanced"]
        Y2 --> Y3["pick every downstream option<br/>by that same strategy"]
        Y3 --> Y4["one coherent whole plan"]
    end
```

---

## 6. Ranking — five objectives, two mechanisms

```mermaid
flowchart TB
    P["9 candidate plans"] --> PAR["Pareto front<br/>needs no weights at all"]
    P --> SCA["Scalarised score<br/>orders within the front"]
    PAR --> R["Ranked list"]
    SCA --> R
    R --> RC["Reason codes<br/>explain.py"]
```

```
score = money
      + hours          × time value
      + bookings moved × switching cost
      + experience given up
      + fragility      × reliability weight
```

| Objective | Guards against |
| --- | --- |
| Money | — |
| Time | — |
| Disruption | Churn for its own sake |
| **Experience** | Winning by quietly deleting the trip |
| **Fragility** | Calling a plan safe because its *flight* is direct |

**Experience** is priced at `1.5 ×` what was paid. A purchase reveals
willingness-to-pay at or *above* the price, so valuing the loss at exactly the
price makes a refund cancel it out perfectly and "delete the day, take the money
back" reads as free.

**Fragility** compounds across legs, and released bookings stay in the product —
giving up a reserved transfer does not make the evening more reliable, it puts
the member on an unmanaged fallback.

### Where the model actually sits

```mermaid
flowchart LR
    SNAP["Immutable request snapshot"] --> MCP["TripShield Context MCP"]
    MCP --> G["get_trip_graph"]
    MCP --> P["list_candidate_plans"]
    MCP --> H["get_member_choice_history"]
    G & P & H --> MODEL["Claude or GPT-5.6 Sol"]
    MODEL --> VALID["Schema + ID + recommendation validation"]
    VALID -->|valid| COPY["Explanation addendum"]
    VALID -->|invalid / timeout| FALLBACK["Deterministic explanation"]
```

Both providers use the same in-process MCP server and must call all three tools
exactly once. The server exposes no write or transaction tools and no state beyond
the current request. Model output is accepted only when its plan and option IDs are
known and its recommended plan agrees with the deterministic optimizer. It is
generated only for initial planning; interactive reranking returns
`ai.status = "not_requested"`, which prevents stale prose from being displayed.

The active provider is chosen with `AI_PROVIDER=anthropic|openai`. If it is unset,
Anthropic is preferred when configured, then OpenAI. An explicitly selected but
unconfigured provider fails closed rather than crossing providers. Timeouts, rate
limits, tool-loop errors and invalid structured output all preserve the existing
deterministic result.

The inferred profile weights remain deterministic inputs derived from the synthetic
member history. The model contextualizes carrier/connection and activity trade-offs
in prose; it does not modify those weights or the scalarised score.

---

## 7. Human-in-the-loop — the browser never decides

```mermaid
sequenceDiagram
    autonumber
    participant M as Member
    participant UI as Plan editor
    participant API as POST /recovery/plan/validate
    participant G as graph.propagate

    M->>UI: drags the cheap next-morning flight in
    UI->>API: { selections }
    API->>G: re-propagate the whole graph
    G-->>API: 5 hard violations
    API-->>UI: valid=false + why, per booking
    UI-->>M: approval disabled, reasons shown
    M->>UI: repairs hotel, passport, transfer, dinner
    UI->>API: { selections }
    API-->>UI: valid=true, recomputed totals
```

If the frontend were allowed to compute feasibility it would quietly become the
source of truth, and it would be wrong the first time a supplier rule changed.

---

## 8. Execution — a saga, not a batch

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> AWAITING_APPROVAL: costs money
    AWAITING_APPROVAL --> IN_PROGRESS: member authorises
    PENDING --> IN_PROGRESS: no charge
    IN_PROGRESS --> DONE: supplier confirms
    IN_PROGRESS --> FAILED: offer expired
    PENDING --> SKIPPED: run stopped
    DONE --> COMPENSATING: undo requested
    COMPENSATING --> COMPENSATED: supplier reverses
    COMPENSATING --> COMPENSATION_FAILED: cannot be bought back
    DONE --> [*]
```

Steps run **one at a time in dependency order** — the hotel amendment is only
correct once the replacement flight is ticketed. Each charge is authorised
individually; the plan-level approval only queues the work.

### Cancel is two different words

```mermaid
flowchart TB
    CANCEL{"Member presses cancel"}
    CANCEL -->|"Stop here"| STOP["Skip remaining steps.<br/>Committed steps stay committed."]
    CANCEL -->|"Undo everything"| QUOTE["Ask every supplier<br/>what it would really return"]
    QUOTE --> SHOW["Show refundable vs unrecoverable"]
    SHOW --> CONFIRM{"Still want to?"}
    CONFIRM -->|no| STOP
    CONFIRM -->|yes| COMP["Compensate in reverse order"]
```

Rollback is quoted before it runs because it is neither free nor always possible:

| Step type | Reversing it |
| --- | --- |
| Flight change (Duffel) | Refund **net of the airline's fee** |
| Hotel amendment (LiteAPI) | Usually full refund |
| Same-day dining cancellation | Nothing comes back |
| A step that **gave a booking up** | Not a refund at all — a re-purchase at today's price, if the inventory still exists |

---

## 9. Accountability

```mermaid
flowchart LR
    RANK["Ranking"] --> RC["Reason codes<br/>stable vocabulary"]
    PLAN["Approved plan"] --> AUD["Audit record"]
    RC --> AUD
    AUD --> LOG["GET /api/audit"]
```

An audit record is written at **approval**, not at execution — the question
worth answering months later is usually "why was this offered", not "did the
booking succeed". It carries the recommendation, the member's actual choice,
every candidate's score, the Pareto front, the weighting in force, and the model
versions that produced them.

The gap between `recommended_plan_id` and `selected_plan_id` is the single most
useful signal the system produces about whether its ranking matches what members
actually want.

---

## 10. Trust boundaries

| Boundary | Rule |
| --- | --- |
| Frontend → feasibility | The browser proposes; the server decides. Always. |
| API data → outbound links | Allowlisted to `https://…americanexpress.com` at the render boundary, so it holds whichever connector produced the link |
| Supplier → Amex partner | Exact normalized name or explicit alias in the same category and market; never fuzzy inference |
| Plan display → plan approval | `Option` is rebuilt from its own field list, so a new field cannot silently default and make the approved plan differ from the displayed one |
| REST connector reads | AeroDataBox may be live; Duffel and LiteAPI are sandbox; every failure falls back to fixtures with provenance |
| Connector transactions | Fixture-only. A demonstration must not transact against live inventory |
| Model MCP | Exactly three read-only tools over an immutable request snapshot; no public MCP endpoint |
| Execution runs | Never reconstructed from seed data — a missing run returns `410`, because it records transactions that actually happened |

The Amex partner catalog is curated from official Amex pages and records when an
entry was verified. It is updated as reviewed source code, not by runtime scraping.
Viator's official Experiences MCP is documented but deferred; activities remain
fixtures, as do dining and ground transport.

---

## 11. Module map

| Module | Responsibility | Deterministic? |
| --- | --- | --- |
| `domain.py` | Shared vocabulary and types | ✔ |
| `catalog.py` | The member's booking history | ✔ |
| `graph.py` | Dependency graph, propagation, splicing | ✔ |
| `connectors.py` | Direct REST reads, provenance and fixture fallbacks | ✔ (external reads vary) |
| `amex_partners.py` | Curated official-source partner catalog and exact matching | ✔ |
| `mcp_server.py` | Embedded immutable TripShield Context MCP | Read-only |
| `ai.py` | Claude/OpenAI tool loop and structured-output validation | Explanation only |
| `agents.py` | Five specialized recovery subagents | ✔ |
| `orchestrator.py` | The workflow, task creation, plan assembly | ✔ |
| `optimizer.py` | Pareto front and scalarised ranking | Weights are inferred |
| `explain.py` | Reason codes and audit records | ✔ |
| `execution.py` | Saga engine and compensation | ✔ |
| `store.py` | Session state and audit trail | ✔ |

---

*Independent AMEX AI Hackathon 2026 concept. Not affiliated with or endorsed by
American Express. Booking references and transactions are synthetic; read-only
availability can come from named live/sandbox providers and is labelled by provenance.*
