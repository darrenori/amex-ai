// Stage 2 — the trip dependency graph.
//
// The edges are the content: two bookings can sit next to each other in time and
// depend on nothing; two days apart and one can't happen without the other.
// Severity is carried by the edge (solid HARD / dashed SOFT) and the buffer it
// demands by its label; node colour follows the booking's fate under the
// disruption. Drawn as a self-contained SVG so it renders reliably as an island,
// with the accessible table beneath it carrying the same information.

import { useMemo } from 'react';

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

const NODE_W = 210;
const NODE_H = 70;
const GAP_X = 36;
const GAP_Y = 62;
const PAD = 20;

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
  const pos = new Map();
  [...rows.keys()].sort((a, b) => a - b).forEach((row) => {
    const items = rows.get(row).sort((a, b) => String(a.start).localeCompare(String(b.start)));
    const span = items.length * NODE_W + (items.length - 1) * GAP_X;
    const left = (width - span) / 2;
    items.forEach((node, i) => {
      pos.set(node.id, { x: left + i * (NODE_W + GAP_X), y: PAD + row * (NODE_H + GAP_Y) });
    });
  });
  const height = PAD * 2 + rows.size * NODE_H + (rows.size - 1) * GAP_Y;
  return { pos, width, height };
}

function edgePath(from, to) {
  const x1 = from.x + NODE_W / 2;
  const y1 = from.y + NODE_H;
  const x2 = to.x + NODE_W / 2;
  const y2 = to.y;
  const lift = Math.max((y2 - y1) * 0.45, 20);
  return `M ${x1} ${y1} C ${x1} ${y1 + lift}, ${x2} ${y2 - lift}, ${x2} ${y2}`;
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
  if (v < 60) return `${v} min`;
  return v % 60 ? `${Math.floor(v / 60)}h ${v % 60}m` : `${Math.floor(v / 60)}h`;
};

export default function DependencyGraph({ graph, assessment, currency = 'SGD' }) {
  const { nodes, edges, pos, width, height } = useMemo(() => {
    const gNodes = graph?.nodes ?? [];
    const gEdges = graph?.edges ?? [];
    const { pos, width, height } = layout(gNodes, gEdges);
    return { nodes: gNodes, edges: gEdges, pos, width, height };
  }, [graph]);

  const verdicts = assessment?.verdicts ?? {};

  return (
    <div className="depgraph-scroll">
      <svg
        className="depgraph"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={`Trip dependency graph: ${nodes.length} bookings joined by ${edges.length} dependencies. The same information is in the table below.`}
      >
        <defs>
          <marker id="dg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.6 L7.4 4 L0 7.4 z" fill="var(--ink-muted)" />
          </marker>
          <marker id="dg-arrow-soft" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.6 L7.4 4 L0 7.4 z" fill="var(--ink-subdued)" />
          </marker>
        </defs>

        {edges.map((e, i) => {
          const from = pos.get(e.source);
          const to = pos.get(e.target);
          if (!from || !to) return null;
          const hard = e.severity === 'hard';
          const midX = (from.x + to.x) / 2 + NODE_W / 2;
          const midY = (from.y + NODE_H + to.y) / 2;
          const label = `${hard ? 'HARD' : 'SOFT'} · ${bufferLabel(e.min_buffer_minutes)}`;
          return (
            <g key={i}>
              <title>{e.rationale}</title>
              <path
                d={edgePath(from, to)}
                fill="none"
                stroke={hard ? 'var(--ink-muted)' : 'var(--ink-subdued)'}
                strokeWidth={hard ? 2 : 1.5}
                strokeDasharray={hard ? undefined : '5 5'}
                markerEnd={`url(#${hard ? 'dg-arrow' : 'dg-arrow-soft'})`}
              />
              <rect
                x={midX - label.length * 3.4} y={midY - 9}
                width={label.length * 6.8} height={18} rx={9}
                fill="var(--canvas)" stroke="var(--border-subtle)"
              />
              <text x={midX} y={midY + 4} textAnchor="middle"
                    style={{ fontSize: 10, fontWeight: 600, fill: 'var(--ink-muted)' }}>
                {label}
              </text>
            </g>
          );
        })}

        {nodes.map((n) => {
          const p = pos.get(n.id);
          if (!p) return null;
          const status = verdicts[n.id]?.status ?? n.status;
          const tone = TONE[STATUS_TONE[status] ?? 'neutral'];
          return (
            <g key={n.id} transform={`translate(${p.x} ${p.y})`}>
              <title>{n.title}</title>
              <rect width={NODE_W} height={NODE_H} rx={12}
                    fill="var(--canvas)" stroke="var(--border)"
                    style={{ filter: 'drop-shadow(0 1px 3px rgba(0,23,90,0.10))' }} />
              <rect width={4} height={NODE_H} rx={2} fill={tone} />
              <text x={16} y={22} style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', fill: 'var(--ink-subdued)' }}>
                {n.label.toUpperCase()}
              </text>
              <text x={16} y={41} style={{ fontSize: 13, fontWeight: 600, fill: 'var(--ink)' }}>
                {truncate(n.title, 26)}
              </text>
              <text x={16} y={58} style={{ fontSize: 11, fill: 'var(--ink-muted)' }}>
                {stamp(n.start)} · {money(n.amount, currency)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
