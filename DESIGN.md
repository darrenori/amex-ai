---
version: alpha
name: American Express
description: >-
  A premium financial identity anchored by Amex Blue (#006FCF) and deep navy
  (#00175A), where restrained blue authority, generous whitespace, and Benton
  Sans's measured forms communicate trust, prestige, and institutional
  permanence across membership experiences.
colors:
  primary: '#006FCF'
  on-primary: '#FFFFFF'
  primary-hover: '#1374D4'
  primary-pressed: '#00509E'
  navy: '#00175A'
  navy-deep: '#000C3D'
  ink: '#1A1A1A'
  ink-muted: '#53565A'
  ink-subdued: '#86888C'
  ink-on-navy: '#FFFFFF'
  ink-on-navy-muted: '#B7C3D9'
  canvas: '#FFFFFF'
  surface-1: '#F7F8F9'
  surface-2: '#ECEDEE'
  border: '#D5D9DC'
  border-subtle: '#ECEDEE'
  success: '#00875A'
  warning: '#B95000'
  error: '#C52720'
  gold: '#BF9B30'
typography:
  display:
    fontFamily: 'Benton Sans, Helvetica Neue, Helvetica, Arial, sans-serif'
    fontSize: 40px
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: '-0.01em'
  body:
    fontFamily: 'Benton Sans, Helvetica Neue, Helvetica, Arial, sans-serif'
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: 0em
spacing:
  base: 8px
  scale: [4, 8, 12, 16, 24, 32, 48, 64, 96, 128]
radius:
  sm: 4px
  md: 8px
  lg: 12px
  pill: 9999px
shadows:
  card: '0 1px 4px rgba(0,23,90,0.10)'
  elevated: '0 6px 24px rgba(0,23,90,0.16)'
motion:
  duration-fast: 120ms
  duration-base: 240ms
  easing: 'cubic-bezier(0.4, 0, 0.2, 1)'
source: 'https://www.designmd.co/d/american-express'
sourceUrl: 'https://www.americanexpress.com'
updated: '2026-07-21'
---

> Reference design system for this project, sourced from
> [designmd.co/d/american-express](https://www.designmd.co/d/american-express).
> The tokens above are implemented verbatim in `web/src/styles/tokens.css`.
> American Express is a trademark of American Express Company; this repository is
> an independent hackathon concept, not affiliated with or endorsed by Amex.

## Rationale

**Blue as institutional trust** — American Express Blue (#006FCF) is one of finance's most
established equity colors, carried for decades on the Blue Box logo. In the digital product it
functions as the color of authority and action: primary buttons, links, and the brand mark. It
signals permanence, security, and a relationship that predates and will outlast any design fashion.

**Prestige through restraint** — The Amex brand promise is membership and premium service,
expressed not through ornamentation but through discipline. Generous whitespace, measured
typography, a tight color palette, and clean alignment communicate a serious financial institution
worthy of trust with high-value spending. The confidence to use less is itself a luxury signal.

**Deep navy for premium and dark surfaces** — Beyond the everyday blue, Amex reaches for a deep
navy (#00175A) on premium card tiers, statement headers, and dark hero surfaces. White type on navy
is a recurring premium pairing that elevates membership moments above routine transactional screens.

**Clarity for high-stakes financial decisions** — Cardmembers manage real money. Every screen must
present financial information with absolute legibility and zero ambiguity: comfortable body sizes,
clear numeric hierarchy with tabular figures, and unambiguous semantic colors for status.

## 1. Visual theme and atmosphere

Composed, premium, institutional. Predominantly clean white with Amex Blue as the consistent action
and brand color, punctuated by deep navy on premium and hero surfaces. The atmosphere is calm
authority — generous space, restrained color, and confident typography.

The membership experience foregrounds the card and the cardmember's standing. Premium tiers shift
toward darker navy and metallic-gold accents; everyday servicing screens stay bright, clear, and
blue. The depth model is gentle — soft navy-tinted shadows lift cards just enough to organize.

## 2. Color system

**Brand blue** — Amex Blue `#006FCF` (primary buttons, links, brand mark), hover `#1374D4`,
pressed `#00509E`.

**Premium navy** — Navy `#00175A` (premium surfaces, statement headers, dark heroes),
Navy deep `#000C3D` (deepest surfaces, gradients).

**Light surfaces** — Canvas `#FFFFFF`, Surface 1 `#F7F8F9` (page background), Surface 2 `#ECEDEE`
(nested panels, inputs), Border `#D5D9DC`, Border subtle `#ECEDEE`.

**Text on light** — Ink `#1A1A1A`, Muted `#53565A`, Subdued `#86888C`.

**Text on navy** — `#FFFFFF`, muted `#B7C3D9`.

**Premium accent** — Gold `#BF9B30`, tier signaling only, used sparingly.

**Semantic** — Success `#00875A`, Warning `#B95000`, Error `#C52720`.

Amex Blue is the action and identity color; navy is the premium surface. Gold is reserved strictly
for tier signaling — overusing it would cheapen the prestige cue it exists to protect.

## 3. Typography

Benton Sans — a refined, highly legible American grotesque — with Helvetica Neue, Helvetica and
Arial fallbacks. Display scale 28–40px, weight 600, −0.01em tracking. Body 16px, weight 400,
1.55 line height. Monetary figures and account numbers use weight 600 with tabular figures so
balances align precisely. The system avoids hairline weights.

## 4. Components and patterns

- **Card art tile** — the cardmember's card in brand color or premium navy/metal, tier-appropriate
  finish, rounded corners, subtle sheen; tappable to reveal account summary.
- **Account summary panel** — large primary figure with secondary metrics in muted ink, prominent
  primary blue action.
- **Membership Rewards balance** — points in large figures with redemption entry points.
- **Transaction statement list** — merchant, date, category, amount; pending vs. posted clearly
  marked; rows tappable to detail.
- **Primary blue button** — `#006FCF` fill, white text, restrained radius (4–8px); secondary actions
  as blue-outline ghost buttons; one primary per screen.
- **Premium navy hero** — full-width `#00175A` surface, white headlines, gold accents.
- **Offers / benefits card** — white rounded card, value statement, add-to-card action.
- **Status chip** — pill chips in semantic colors; pending / posted / past due / disputed.
- **Secure servicing banner** — lock iconography, calm authoritative styling; security as a premium
  service, not a warning.

## 5. Spacing and layout

8px base grid with generous, premium spacing. Content sits in clean white cards on a soft `#F7F8F9`
background, 24–32px padding inside cards, 24–48px between sections. Page gutters are 16px on mobile
and widen substantially on desktop, keeping a calm, centered reading column. Premium navy heroes
span full width. Statement rows are 56–64px tall with aligned tabular amounts.

## 6. Motion and interaction

- **Composed transitions** — 240ms eased, never bouncy.
- **Button press** — 120ms shift to the pressed blue tone with a subtle scale.
- **Card reveal** — smooth transition to account controls; gentle sheen on premium surfaces.
- **Payment confirmation** — measured check-mark, 240ms reveal; reassuring, not celebratory.
- **Statement expand** — 200ms height transition; no abrupt jumps in a financial list.

## Accessibility

### Contrast ratios

| Pair | Ratio | Result |
| --- | --- | --- |
| `#FFFFFF` on `#006FCF` | 4.6:1 | passes AA |
| `#FFFFFF` on `#00175A` | 15.4:1 | passes AAA |
| `#1A1A1A` on `#FFFFFF` | 17.9:1 | passes AAA |
| `#53565A` on `#FFFFFF` | 7.4:1 | passes AAA |
| `#86888C` on `#FFFFFF` | 3.6:1 | fails AA — non-essential fine print only |
| `#006FCF` on `#FFFFFF` | 4.5:1 | passes AA — links and large text |
| `#B7C3D9` on `#00175A` | 8.9:1 | passes AAA |
| `#BF9B30` on `#FFFFFF` | 2.7:1 | fails AA — decorative tier accent, never text |
| `#1A1A1A` on `#F7F8F9` | 17.0:1 | passes AAA |

### Minimum requirements

- **Touch target** — 44×44px minimum across all controls.
- **Financial figures** — exposed as readable labeled text, never images; tabular alignment must not
  break screen-reader order.
- **Focus indicator** — 2px solid `#006FCF` on light surfaces, 2px solid `#FFFFFF` on navy.
- **Status** — conveyed with text and icon, never semantic color alone.

### Motion

Respects `prefers-reduced-motion`. All motion is non-essential; financial state changes apply
without animation under reduced motion.

### Notes

- Amex Blue on white at 4.5:1 is borderline for small text — prefer it for buttons, large links and
  fills; pair with `#00509E` where smaller blue text is unavoidable.
- Gold must never carry text; tier identity must also be conveyed with a text label.
- Favor navy over saturated blue for any dark surface carrying meaningful text.
- Security and fraud messaging must remain calm and clear; never rely on red alone.
