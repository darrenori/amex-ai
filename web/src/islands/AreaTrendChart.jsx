// bklit-style area chart (Recharts) for the account overview — a calm gradient
// trend with a tight grid and a compact tooltip, styled to the Amex tokens.

import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
} from 'recharts';

function TooltipCard({ active, payload, label, prefix }) {
  if (!active || !payload || !payload.length) return null;
  const value = payload[0].value;
  return (
    <div style={{
      background: 'var(--canvas)', border: '1px solid var(--border)', borderRadius: 8,
      boxShadow: 'var(--shadow-elevated)', padding: '8px 12px', fontFamily: 'var(--font-sans)',
    }}>
      <div style={{ fontSize: 11, color: 'var(--ink-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink)', fontVariantNumeric: 'tabular-nums' }}>
        {prefix}{Number(value).toLocaleString('en-SG')}
      </div>
    </div>
  );
}

export default function AreaTrendChart({ data = [], prefix = '', color = '#006FCF', height = 220 }) {
  return (
    <div style={{ width: '100%' }}>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.28} />
              <stop offset="100%" stopColor={color} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--border-subtle)" vertical={false} />
          <XAxis
            dataKey="label"
            tickLine={false}
            axisLine={{ stroke: 'var(--border)' }}
            tick={{ fill: 'var(--ink-subdued)', fontSize: 12, fontFamily: 'var(--font-sans)' }}
            dy={6}
          />
          <YAxis
            width={44}
            tickLine={false}
            axisLine={false}
            tick={{ fill: 'var(--ink-subdued)', fontSize: 11, fontFamily: 'var(--font-sans)' }}
            tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : v)}
          />
          <Tooltip cursor={{ stroke: 'var(--border)', strokeWidth: 1 }} content={<TooltipCard prefix={prefix} />} />
          <Area
            type="monotone"
            dataKey="value"
            stroke={color}
            strokeWidth={2}
            fill="url(#areaFill)"
            activeDot={{ r: 4, fill: color, stroke: 'var(--canvas)', strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
