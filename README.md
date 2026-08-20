# TripShield

Whole-trip disruption recovery for American Express Card Members. An independent
concept prepared for the **AMEX AI Hackathon 2026** — not an official American Express
product, and not affiliated with or endorsed by American Express. Every flight number,
price, timing, merchant and preference weight in this repository is synthetic.

## The idea

When a flight is cancelled, most tools re-price the flight. But the hotel and the car
rental for that trip were charged to the same Card, so the disruption has a cost the
airline never sees. TripShield prices each recovery path against the **whole journey**,
then ranks the paths using a time-value weight **inferred from the member's own past
choices** rather than a preference they were asked to set.

```
Personalized score = whole-trip financial impact + (hours lost × inferred time value)
```

Lower is better. The same cancellation resolves to a different plan for a different
history — which is the point of the inspection toggle in section 4.

| Path | Fare | Hotel | Car | Hours | Net impact |
| --- | ---: | ---: | ---: | ---: | ---: |
| Same-night rebooking | +SGD 180 | 0 | 0 | 4 | **+SGD 180** |
| Next-morning rebooking | −SGD 400 | +SGD 300 | 0 | 13 | **−SGD 100** |
| Two-day rebooking | −SGD 1,150 | +SGD 600 | +SGD 120 | 48 | **−SGD 430** |

Recommendations by inferred weight: SGD 45/hr → same-night, SGD 25/hr → next-morning,
SGD 8/hr → two-day. Three different answers to one cancellation.

## What this repository is

Two standalone prototypes were merged into one application:

- `legacy/amex-travel-login.html` — the AMEX Travel sign-in, account overview and
  recovery flow (alert, countdown, review-before-anything-changes dialog, confirm/undo).
- `legacy/tripshield-dashboard.html` — the TripShield analysis: trip snapshot, net
  financial impact, personalized scoring and the recovery journey.

Both are kept for reference. The live application is the Vite frontend in `web/`
talking to the FastAPI backend in `api/`, restyled onto the American Express design
system in [`DESIGN.md`](DESIGN.md).

## Design direction

Three directions were considered against `DESIGN.md`:

1. **Full-navy dark application.** Rejected — the Amex system is explicitly a light
   theme; navy is a hero and premium-tier surface, not a page background.
2. **Two-surface: navy hero → white servicing.** *Chosen.* Deep navy carries the
   pre-auth membership moment (and the airport approach artwork from the original file,
   recoloured to the navy ramp); everything post-login sits as white cards on
   `#F7F8F9`, where financial figures are actually read.
3. **Dense analyst console** — the original TripShield density. Rejected as the primary
   frame: it works against "whitespace as a luxury signal". The density is retained
   only inside the *Behind the scenes* panel, where compactness is the point.

Aesthetic brief: composed institutional calm. Amex Blue `#006FCF` is the only action
colour and appears once per decision; deep navy `#00175A` marks membership moments;
gold `#BF9B30` appears only as a tier rule and never carries text. Type is the Benton
Sans stack at 16/1.55 body and a `clamp(28px, 4.4vw, 40px)` display, with
`tabular-nums` on every figure so amounts align down a column. Spacing is the 8px
scale, radii stay at 4–12px, motion is 240ms base / 120ms press and fully disabled
under `prefers-reduced-motion`. Status is always text plus icon, never colour alone.

## Getting started

```bash
npm install
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```

Run the two processes in separate terminals:

```bash
source .venv/bin/activate
npm run dev:api
```

```bash
npm run dev
```

The frontend is served at `http://localhost:5173` and proxies `/api` to the backend on
port 8000. Interactive API docs are at `http://localhost:8000/api/docs`.

Demo credentials: `demo@amextravel.com` / `Travel123!`

## Layout

```
api/index.py            FastAPI app — domain model, scoring, all /api routes
web/index.html          Shell: login markup and the navy hero artwork
web/src/main.js         Bootstrap: login, view switching, restart
web/src/api.js          Typed fetch client for the backend
web/src/format.js       Currency, sign convention, tabular figures
web/src/icons.js        Inline SVG set (no icon font, no external requests)
web/src/components/     Shared dialog with focus trap
web/src/views/          account · recovery · intelligence · journey
web/src/styles/         tokens.css (DESIGN.md verbatim) · base.css · app.css
legacy/                 The two original standalone prototypes
DESIGN.md               The American Express design system this is built on
```

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Platform-neutral liveness |
| `GET` | `/api/health` | Liveness |
| `POST` | `/api/auth/login` | Demo credential check |
| `GET` | `/api/account` | Member, card, transactions, benefits, trip |
| `GET` | `/api/trip` | The confirmed trip and its components |
| `GET` | `/api/trips/TRIP-001` | Spec-compatible trip resource |
| `GET` | `/api/benefits` | Membership benefits |
| `GET` | `/api/profiles` | Inferred traveler profiles and their histories |
| `POST` | `/api/disruptions` | Create the synthetic cancellation or delay event |
| `POST` | `/api/recommendations` | Run impact, guardrails, preference, reliability and ranking |
| `POST` | `/api/disruption/simulate` | Fire the cancellation, return the scored recovery set |
| `GET` | `/api/recovery?profile_id=` | Re-score the paths for a different inferred weight |
| `GET` | `/api/recovery/TRIP-001` | Spec-compatible recovery result |
| `POST` | `/api/recovery/hold` | Hold a replacement seat for 90 minutes |
| `POST` | `/api/recovery/confirm` | Confirm a path, return the booking reference |
| `GET` | `/api/recommendations/TRIP-001/audit` | Recommendation and confirmation audit records |

The ranker now returns a transparent breakdown for financial impact, time penalty,
predicted reliability risk, stop penalty and cabin-change penalty. Confirmation returns
Saga-style simulated execution steps; no real booking, payment, refund or claim is made.

## Deployment

Vercel builds `web/` to `dist/` and serves `api/index.py` as a Python serverless
function; `vercel.json` rewrites `/api/*` onto it.

```bash
npx vercel --prod
```

## Licence

MIT — see [LICENSE](LICENSE). American Express is a trademark of American Express
Company. Design tokens in `DESIGN.md` are sourced from
[designmd.co](https://www.designmd.co/d/american-express).
