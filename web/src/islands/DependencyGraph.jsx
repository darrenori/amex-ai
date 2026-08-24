// Stage 2 — the trip dependency graph, as an interactive blueprint board.
//
// The edges are the content: two bookings can sit next to each other in time and
// depend on nothing; two days apart and one can't happen without the other.
// Severity is carried by the edge (solid HARD / dashed SOFT) and the buffer it
// demands by its label; node colour follows the booking's fate under the
// disruption.
//
// It is a real graph, so you can work it like one: drag a booking to untangle a
// crossing, or click it to trace its chain — everything it depends on upstream
// and everything that depends on it downstream lights up, the rest recedes. The
// accessible table beneath carries the identical information for anyone who
// can't (or would rather not) push nodes around.

import { useMemo, useRef, useState } from 'react';

const TONE = {
  critical: 'var(--error)',
  warn: 'var(--warning)',
  accent: 'var(--primary)',
  good: 'var(--success)',
  neutral: 'var(--border)',
};

const STATUS_TONE = {
  cancelled: 'critical', broken: 'critical', at_risk: 'warn',
  rebooked: 'accent', unaffected: 'good', confirmed: 'good', on_track: 'good',
};

const NODE_W = 216;
const NODE_H = 78;
const GAP_X = 44;
const GAP_Y = 68;
const PAD = 26;
const EDGE = 6; // drag margin inside the canvas

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

/** Longest path from any root, so an edge never points back up the page. */
function depths(nodes, edges) {
  const incoming = new Map(nodes.map((n) => [n.id, []]));
  edges.forEach((e) => incoming.get(e.target)?.push(e.source));
  const depth = new Map();
  const resolve = (id, seen = new Set()) => {
    if (depth.has(id)) return depth.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    const parents = incoming.get(id) ?? [];
    const value = parents.length ? Math.max(...parents.map((p) => resolve(p, seen) + 1)) : 0;
    depth.set(id, value);
    return value;
  };
  nodes.forEach((n) => resolve(n.id));
  return depth;
}

function layout(nodes, edges) {
  const depth = depths(nodes, edges);
  const rows = new Map();
  nodes.forEach((node) => {
    const row = depth.get(node.id) ?? 0;
    if (!rows.has(row)) rows.set(row, []);
    rows.get(row).push(node);
  });
  const widest = Math.max(...[...rows.values()].map((r) => r.length), 1);
  const width = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const base = new Map();
  [...rows.keys()].sort((a, b) => a - b).forEach((row) => {
    const items = rows.get(row).sort((a, b) => String(a.start).localeCompare(String(b.start)));
    const span = items.length * NODE_W + (items.length - 1) * GAP_X;
    const left = (width - span) / 2;
    items.forEach((node, i) => {
      base.set(node.id, { x: left + i * (NODE_W + GAP_X), y: PAD + row * (NODE_H + GAP_Y) });
    });
  });
  const height = PAD * 2 + rows.size * NODE_H + (rows.size - 1) * GAP_Y;
  return { base, width, height };
}

/** Bottom-centre of `from`, top-centre of `to` — where an edge attaches. */
const ends = (from, to) => ({
  a: { x: from.x + NODE_W / 2, y: from.y + NODE_H },
  b: { x: to.x + NODE_W / 2, y: to.y },
});
const lift = (a, b) => Math.max((b.y - a.y) * 0.45, 22);
const pathD = (a, b) => {
  const l = lift(a, b);
  return `M ${a.x} ${a.y} C ${a.x} ${a.y + l}, ${b.x} ${b.y - l}, ${b.x} ${b.y}`;
};
/** Point on the cubic at t — labels sit near the source so parallels don't stack. */
function pointAt(a, b, t) {
  const l = lift(a, b);
  const p1 = { x: a.x, y: a.y + l };
  const p2 = { x: b.x, y: b.y - l };
  const m = 1 - t;
  return {
    x: m * m * m * a.x + 3 * m * m * t * p1.x + 3 * m * t * t * p2.x + t * t * t * b.x,
    y: m * m * m * a.y + 3 * m * m * t * p1.y + 3 * m * t * t * p2.y + t * t * t * b.y,
  };
}

/** Reachable set from `start` along `adj`, excluding the start itself. */
function reach(start, adj) {
  const out = new Set();
  const stack = [start];
  while (stack.length) {
    for (const next of adj.get(stack.pop()) ?? []) {
      if (next !== start && !out.has(next)) { out.add(next); stack.push(next); }
    }
  }
  return out;
}

const money = (v, currency) =>
  `${v < 0 ? '-' : ''}${currency} ${Math.abs(Math.round(v)).toLocaleString('en-SG')}`;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function stamp(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(iso ?? ''));
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[4]}:${m[5]}` : '';
}

const truncate = (v, max) => (v.length > max ? `${v.slice(0, max - 1)}…` : v);

const bufferLabel = (mins) => {
  const v = Math.abs(Math.round(mins ?? 0));
  if (v < 60) return `${v}m`;
  return v % 60 ? `${Math.floor(v / 60)}h ${v % 60}m` : `${Math.floor(v / 60)}h`;
};

/** Small kind glyph, drawn in a 24×24 frame and dropped into the node badge. */
function KindGlyph({ kind }) {
  const shape = {
    flight: <path d="M3 11 21 4 13 20 11 13Z" fill="currentColor" stroke="none" />,
    lodging: <><rect x="3" y="10" width="18" height="6" rx="1.4" /><path d="M3 16v3M21 16v3" /><rect x="5.5" y="7" width="6" height="4" rx="1" /></>,
    activity: <path d="M4 8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v1a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4z" />,
    dining: <><rect x="4" y="8" width="12" height="9" rx="2" /><path d="M16 10h2a2 2 0 0 1 0 4h-2" /></>,
    ground: <><rect x="6" y="4.5" width="12" height="11" rx="2.5" /><path d="M6 11h12M8.5 19l1.5-3M15.5 19l-1.5-3" /></>,
  }[kind] ?? <circle cx="12" cy="12" r="7" />;
  return (
    <svg x={NODE_W - 39} y={13} width="22" height="22" viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      {shape}
    </svg>
  );
}

export default function DependencyGraph({ graph, assessment, currency = 'SGD' }) {
  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];
  const verdicts = assessment?.verdicts ?? {};

  const { base, width, height } = useMemo(() => layout(nodes, edges), [graph]);

  const [override, setOverride] = useState({});
  const [selected, setSelected] = useState(null);
  const svgRef = useRef(null);
  const drag = useRef(null);

  const posOf = (id) => override[id] ?? base.get(id) ?? { x: 0, y: 0 };
  const dirty = Object.keys(override).length > 0;

  // Chain of the selected booking: everything it needs (up) and everything that
  // needs it (down). `set` drives the highlight; counts drive the readout.
  const trace = useMemo(() => {
    if (!selected) return null;
    const out = new Map();
    const inc = new Map();
    edges.forEach((e) => {
      (out.get(e.source) ?? out.set(e.source, []).get(e.source)).push(e.target);
      (inc.get(e.target) ?? inc.set(e.target, []).get(e.target)).push(e.source);
    });
    const down = reach(selected, out);
    const up = reach(selected, inc);
    return { set: new Set([selected, ...down, ...up]), up: up.size, down: down.size };
  }, [selected, edges]);

  const nodeById = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const selNode = selected ? nodeById.get(selected) : null;

  // --- pointer dragging ---------------------------------------------------
  const onDown = (e, id) => {
    if (e.button != null && e.button !== 0) return;
    const rect = svgRef.current?.getBoundingClientRect();
    const scale = rect && rect.width ? width / rect.width : 1;
    const p = posOf(id);
    drag.current = { id, sx: e.clientX, sy: e.clientY, ox: p.x, oy: p.y, scale, moved: false };
    e.currentTarget.setPointerCapture?.(e.pointerId);
    e.stopPropagation();
  };
  const onMove = (e) => {
    const d = drag.current;
    if (!d) return;
    if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 3) d.moved = true;
    if (!d.moved) return;
    const nx = clamp(d.ox + (e.clientX - d.sx) * d.scale, EDGE, width - NODE_W - EDGE);
    const ny = clamp(d.oy + (e.clientY - d.sy) * d.scale, EDGE, height - NODE_H - EDGE);
    setOverride((o) => ({ ...o, [d.id]: { x: nx, y: ny } }));
  };
  const onUp = (e, id) => {
    const d = drag.current;
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    if (d && !d.moved) setSelected((s) => (s === id ? null : id)); // a click, not a drag
  };
  const onKey = (e, id) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setSelected((s) => (s === id ? null : id));
    } else if (e.key.startsWith('Arrow')) {
      e.preventDefault();
      const p = posOf(id);
      const step = 14;
      const nx = clamp(p.x + (e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0), EDGE, width - NODE_W - EDGE);
      const ny = clamp(p.y + (e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0), EDGE, height - NODE_H - EDGE);
      setOverride((o) => ({ ...o, [id]: { x: nx, y: ny } }));
    }
  };

  if (!nodes.length) return null;

  // Draw dimmed edges first so highlighted ones sit on top at crossings.
  const drawEdges = trace
    ? [...edges].sort((x, y) => {
        const cx = trace.set.has(x.source) && trace.set.has(x.target) ? 1 : 0;
        const cy = trace.set.has(y.source) && trace.set.has(y.target) ? 1 : 0;
        return cx - cy;
      })
    : edges;

  return (
    <div className="depgraph-island">
      <div className="depgraph-bar">
        <p className="depgraph-hint" aria-live="polite">
          {selNode ? (
            <>
              <span className="depgraph-hint-node">{truncate(selNode.title, 34)}</span>
              <span className="depgraph-hint-chain">{trace.up} upstream · {trace.down} downstream</span>
            </>
          ) : (
            'Drag to rearrange · click a booking to trace its chain'
          )}
        </p>
        <div className="depgraph-tools">
          {selected && (
            <button type="button" className="depgraph-btn" onClick={() => setSelected(null)}>Clear</button>
          )}
          {dirty && (
            <button type="button" className="depgraph-btn" onClick={() => setOverride({})}>
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                   strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M4 9h11a5 5 0 0 1 0 10h-4" /><path d="m8 5-4 4 4 4" />
              </svg>
              Reset
            </button>
          )}
        </div>
      </div>

      <div className="depgraph-scroll">
        <svg
          ref={svgRef}
          className="depgraph depgraph-svg"
          viewBox={`0 0 ${width} ${height}`}
          width={width}
          height={height}
          style={{ touchAction: 'none' }}
          role="img"
          aria-label={`Trip dependency graph: ${nodes.length} bookings joined by ${edges.length} dependencies. The same information is in the table below.`}
          onPointerDown={() => setSelected(null)}
        >
          <defs>
            <pattern id="dg-grid" width="26" height="26" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" fill="var(--navy)" opacity="0.10" />
            </pattern>
            <marker id="dg-arrow" viewBox="0 0 8 8" refX="6.6" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0.6 7.4 4 0 7.4z" fill="var(--ink-muted)" />
            </marker>
            <marker id="dg-arrow-soft" viewBox="0 0 8 8" refX="6.6" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0.6 7.4 4 0 7.4z" fill="var(--ink-subdued)" />
            </marker>
            <marker id="dg-arrow-on" viewBox="0 0 8 8" refX="6.6" refY="4" markerWidth="7.4" markerHeight="7.4" orient="auto">
              <path d="M0 0.6 7.4 4 0 7.4z" fill="var(--primary)" />
            </marker>
          </defs>

          <rect x="0" y="0" width={width} height={height} fill="url(#dg-grid)" />

          {drawEdges.map((e, i) => {
            const from = posOf(e.source);
            const to = posOf(e.target);
            if (!from || !to) return null;
            const hard = e.severity === 'hard';
            const { a, b } = ends(from, to);
            const active = trace ? trace.set.has(e.source) && trace.set.has(e.target) : false;
            const dim = trace && !active;
            const lp = pointAt(a, b, 0.42);
            const label = `${hard ? 'HARD' : 'SOFT'} · ${bufferLabel(e.min_buffer_minutes)}`;
            const stroke = active ? 'var(--primary)' : hard ? 'var(--ink-muted)' : 'var(--ink-subdued)';
            const marker = active ? 'dg-arrow-on' : hard ? 'dg-arrow' : 'dg-arrow-soft';
            return (
              <g key={i} className={`dg-edge${dim ? ' is-dim' : ''}`}>
                <title>{e.rationale}</title>
                <path
                  className={active && hard ? 'dg-flow' : undefined}
                  d={pathD(a, b)}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={active ? (hard ? 2.6 : 2) : hard ? 2 : 1.5}
                  strokeDasharray={active && hard ? '7 6' : hard ? undefined : '5 5'}
                  markerEnd={`url(#${marker})`}
                />
                <g transform={`translate(${lp.x} ${lp.y})`}>
                  <rect
                    x={-label.length * 3.35} y={-9}
                    width={label.length * 6.7} height={18} rx={9}
                    fill={active ? 'var(--primary-soft)' : 'var(--canvas)'}
                    stroke={active ? 'var(--primary)' : 'var(--border-subtle)'}
                  />
                  <text x={0} y={4} textAnchor="middle"
                        style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.03em', fill: active ? 'var(--primary)' : 'var(--ink-muted)' }}>
                    {label}
                  </text>
                </g>
              </g>
            );
          })}

          {nodes.map((n) => {
            const p = posOf(n.id);
            if (!p) return null;
            const status = verdicts[n.id]?.status ?? n.status;
            const tone = TONE[STATUS_TONE[status] ?? 'neutral'];
            const isSel = n.id === selected;
            const dim = trace && !trace.set.has(n.id);
            return (
              <g
                key={n.id}
                className={`dg-node${dim ? ' is-dim' : ''}${isSel ? ' is-selected' : ''}`}
                transform={`translate(${p.x} ${p.y})`}
                role="button"
                tabIndex={0}
                aria-pressed={isSel}
                aria-label={`${n.title}. Press to trace its dependency chain; arrow keys to move.`}
                onPointerDown={(e) => onDown(e, n.id)}
                onPointerMove={onMove}
                onPointerUp={(e) => onUp(e, n.id)}
                onKeyDown={(e) => onKey(e, n.id)}
              >
                <title>{n.title}</title>
                {isSel && (
                  <rect x={-4} y={-4} width={NODE_W + 8} height={NODE_H + 8} rx={16}
                        fill="none" stroke="var(--primary)" strokeWidth="2" />
                )}
                <rect className="dg-card" width={NODE_W} height={NODE_H} rx={13}
                      fill="var(--canvas)" stroke={isSel ? 'var(--primary)' : 'var(--border)'} />
                <path d={`M0 4 a4 4 0 0 1 4 -4 v${NODE_H - 8} a4 4 0 0 1 -4 4 z`} fill={tone} />
                <circle cx={NODE_W - 28} cy={24} r={14} fill="var(--surface-1)" stroke="var(--border-subtle)" />
                <g style={{ color: 'var(--ink-subdued)' }}><KindGlyph kind={n.kind} /></g>
                <text x={18} y={25} style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.09em', fill: 'var(--ink-subdued)' }}>
                  {String(n.label ?? '').toUpperCase()}
                </text>
                <text x={18} y={45} style={{ fontSize: 13.5, fontWeight: 700, fill: 'var(--ink)' }}>
                  {truncate(n.title, 22)}
                </text>
                <text x={18} y={63} style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', fill: 'var(--ink-muted)' }}>
                  {stamp(n.start)} · {money(n.amount, currency)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
