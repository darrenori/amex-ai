// Stage 2 — the reconstructed dependency graph.
//
// Drawn as a graph rather than listed as an itinerary because the *edges* are
// the content. Two bookings can sit next to each other in time and have no
// relationship at all; two others can be days apart and one of them cannot
// happen without the other. Severity is carried by line weight and dash, buffer
// by the label, and the whole thing is mirrored underneath as a real table so
// nothing here is only available to people who can see it.

import { escapeHtml, minutesLabel, money, stamp } from '../format.js';
import { icons, kindIcon, statusLabel, statusTone } from '../icons.js';

const NODE_W = 208;
const NODE_H = 66;
const GAP_X = 32;
const GAP_Y = 58;
const PAD = 16;

/** Longest path from any root — so an edge never points backwards up the page. */
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

  const widest = Math.max(...[...rows.values()].map((r) => r.length));
  const width = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const positions = new Map();

  [...rows.keys()].sort((a, b) => a - b).forEach((row) => {
    const items = rows.get(row).sort((a, b) => a.start.localeCompare(b.start));
    const span = items.length * NODE_W + (items.length - 1) * GAP_X;
    const left = (width - span) / 2;
    items.forEach((node, index) => {
      positions.set(node.id, {
        x: left + index * (NODE_W + GAP_X),
        y: PAD + row * (NODE_H + GAP_Y),
      });
    });
  });

  const height = PAD * 2 + rows.size * NODE_H + (rows.size - 1) * GAP_Y;
  return { positions, width, height };
}

function edgePath(from, to) {
  const x1 = from.x + NODE_W / 2;
  const y1 = from.y + NODE_H;
  const x2 = to.x + NODE_W / 2;
  const y2 = to.y;
  const lift = Math.max((y2 - y1) * 0.45, 18);
  return `M ${x1} ${y1} C ${x1} ${y1 + lift}, ${x2} ${y2 - lift}, ${x2} ${y2}`;
}

function nodeTone(node, verdict) {
  const status = verdict?.status ?? node.status;
  return { tone: statusTone[status] ?? 'neutral', status };
}

export function graphMarkup(graph, assessment, currency) {
  const nodes = graph.nodes;
  const edges = graph.edges;
  const verdicts = assessment?.verdicts ?? {};
  const { positions, width, height } = layout(nodes, edges);

  const edgeSvg = edges.map((edge) => {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) return '';
    const hard = edge.severity === 'hard';
    const midX = (from.x + to.x) / 2 + NODE_W / 2;
    const midY = (from.y + NODE_H + to.y) / 2;
    const label = `${hard ? 'HARD' : 'SOFT'} · ${minutesLabel(edge.min_buffer_minutes)}`;
    return `
      <g class="edge edge-${hard ? 'hard' : 'soft'}">
        <title>${escapeHtml(edge.rationale)}</title>
        <path d="${edgePath(from, to)}" marker-end="url(#arrow${hard ? '' : 'Soft'})"/>
        <rect x="${(midX - label.length * 3.4).toFixed(1)}" y="${(midY - 9).toFixed(1)}"
              width="${(label.length * 6.8).toFixed(1)}" height="18" rx="9" class="edge-chip"/>
        <text x="${midX.toFixed(1)}" y="${(midY + 4).toFixed(1)}" text-anchor="middle">${label}</text>
      </g>`;
  }).join('');

  const nodeSvg = nodes.map((node) => {
    const p = positions.get(node.id);
    const { tone, status } = nodeTone(node, verdicts[node.id]);
    return `
      <g class="gnode gnode-${tone}" transform="translate(${p.x} ${p.y})">
        <title>${escapeHtml(node.title)} — ${escapeHtml(statusLabel[status] ?? status)}</title>
        <rect width="${NODE_W}" height="${NODE_H}" rx="10"/>
        <rect width="4" height="${NODE_H}" rx="2" class="gnode-spine"/>
        <text class="gnode-label" x="16" y="24">${escapeHtml(node.label.toUpperCase())}</text>
        <text class="gnode-title" x="16" y="43">${escapeHtml(truncate(node.title, 26))}</text>
        <text class="gnode-time" x="16" y="58">${escapeHtml(stamp(node.start))} · ${escapeHtml(money(node.amount, currency))}</text>
      </g>`;
  }).join('');

  return `
    <div class="graph-frame">
      <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"
           role="img" aria-labelledby="graphTitle graphDesc" class="depgraph">
        <title id="graphTitle">Trip dependency graph</title>
        <desc id="graphDesc">
          ${escapeHtml(nodes.length)} bookings joined by ${escapeHtml(edges.length)} dependencies.
          The same information is listed in the table below this diagram.
        </desc>
        <defs>
          <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.6 L7.4 4 L0 7.4 z" fill="var(--ink-muted)"/>
          </marker>
          <marker id="arrowSoft" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0 0.6 L7.4 4 L0 7.4 z" fill="var(--ink-subdued)"/>
          </marker>
        </defs>
        ${edgeSvg}
        ${nodeSvg}
      </svg>
    </div>

    <div class="graph-legend">
      <span><i class="key key-hard" aria-hidden="true"></i>Hard — a violation invalidates the booking</span>
      <span><i class="key key-soft" aria-hidden="true"></i>Soft — a violation only degrades it</span>
      <span><i class="key key-buffer" aria-hidden="true"></i>Label is the minimum buffer the edge demands</span>
    </div>`;
}

function truncate(value, max) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

export function impactMarkup(graph, assessment, currency) {
  if (!assessment) {
    return '<p class="muted stage-note">Run the detection sweep to see what the disruption reaches.</p>';
  }

  const rows = graph.nodes.map((node) => {
    const verdict = assessment.verdicts[node.id];
    const status = verdict?.status ?? node.status;
    const tone = statusTone[status] ?? 'neutral';
    const slack = verdict?.slack_minutes;
    return `
      <tr class="impact-${tone}">
        <th scope="row">
          <span class="icon-badge ${tone}" aria-hidden="true">${kindIcon[node.kind] ?? icons.plane}</span>
          <span>
            <span class="impact-title">${escapeHtml(node.title)}</span>
            <span class="impact-sub">${escapeHtml(node.supplier)} · ${escapeHtml(stamp(node.start))}</span>
          </span>
        </th>
        <td><span class="chip chip-${tone}"><span class="dot"></span>${escapeHtml(statusLabel[status] ?? status)}</span></td>
        <td class="num">${slack === null || slack === undefined
          ? '—'
          : `<span class="${slack < 0 ? 'crit' : 'pos'}">${slack < 0 ? '−' : '+'}${minutesLabel(slack)}</span>`}</td>
        <td class="num">${verdict?.exposure ? `<span class="neg">${escapeHtml(money(verdict.exposure, currency))}</span>` : '—'}</td>
        <td class="reason">${escapeHtml(verdict?.reason ?? '')}</td>
      </tr>`;
  }).join('');

  return `
    <div class="table-scroll">
      <table class="impact-table">
        <caption class="sr-only">Every booking on the trip and how the cancellation reaches it</caption>
        <thead>
          <tr>
            <th scope="col">Booking</th>
            <th scope="col">Status</th>
            <th scope="col" class="num">Margin</th>
            <th scope="col" class="num">At risk</th>
            <th scope="col">Why</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p class="graph-total">
      Doing nothing costs <strong class="neg">${escapeHtml(money(assessment.exposure, currency))}</strong>
      in non-refundable spend across ${assessment.affected.length} affected booking${assessment.affected.length === 1 ? '' : 's'}.
    </p>`;
}
