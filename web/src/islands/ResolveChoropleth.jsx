// The "resolving" map — recovery propagating along the journey.
//
// A choropleth in the bklit style, drawn directly with d3-geo (projection + path)
// and topojson-client, so it carries no d3-zoom/d3-selection and cannot conflict
// with ReactFlow's copies. The trip's countries fill on a scale as their bookings
// are put right; a marker on each leg (Singapore → Tokyo → Osaka) turns amber →
// blue → green as the run commits its steps. A text summary carries the same
// state for screen readers.

import { useMemo } from 'react';
import { geoMercator, geoPath } from 'd3-geo';
import { feature } from 'topojson-client';
import { scaleLinear } from 'd3-scale';
import topo from 'world-atlas/countries-110m.json';

const STATUS = {
  at_risk: { color: '#B95000', label: 'At risk' },
  resolving: { color: '#006FCF', label: 'Resolving' },
  resolved: { color: '#00875A', label: 'Resolved' },
};

const SINGAPORE = '702';
const JAPAN = '392';

const WIDTH = 800;
const HEIGHT = 430;

const fillScale = scaleLinear().domain([0, 0.5, 1]).range(['#ECEDEE', '#AFD3F0', '#8AD3B9']).clamp(true);

const countries = feature(topo, topo.objects.countries);
const projection = geoMercator().center([122, 20]).scale(560).translate([WIDTH / 2, HEIGHT / 2]);
const path = geoPath(projection);

export default function ResolveChoropleth({ regions = [], progress = 0 }) {
  const countryValue = useMemo(() => {
    const val = (ids) => {
      const legs = regions.filter((r) => ids.includes(r.id));
      if (!legs.length) return 0;
      const score = { at_risk: 0, resolving: 0.5, resolved: 1 };
      return legs.reduce((s, r) => s + (score[r.status] ?? 0), 0) / legs.length;
    };
    return { [SINGAPORE]: val(['sg']), [JAPAN]: val(['tokyo', 'osaka']) };
  }, [regions]);

  return (
    <div className="choro-host">
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img"
           aria-label="Journey recovery map by region">
        <g>
          {countries.features.map((geo, i) => {
            const id = String(geo.id);
            const value = countryValue[id];
            const active = value !== undefined;
            return (
              <path
                key={i}
                d={path(geo)}
                fill={active ? fillScale(value) : 'var(--surface-1)'}
                stroke="var(--border)"
                strokeWidth={0.5}
              />
            );
          })}
        </g>
        {regions.map((r) => {
          const tone = STATUS[r.status] ?? STATUS.at_risk;
          const [x, y] = projection(r.coordinates) ?? [0, 0];
          return (
            <g key={r.id} transform={`translate(${x} ${y})`}>
              <circle r={9} fill={tone.color} fillOpacity={0.18} />
              <circle r={4.5} fill={tone.color} stroke="var(--canvas)" strokeWidth={1.5} />
              <text textAnchor="middle" y={-13} style={{
                fontFamily: 'var(--font-sans)', fontSize: 13, fontWeight: 600,
                fill: 'var(--ink)', paintOrder: 'stroke', stroke: 'var(--canvas)', strokeWidth: 3,
              }}>{r.name}</text>
            </g>
          );
        })}
      </svg>

      <div className="choro-legend" aria-hidden="true">
        {Object.values(STATUS).map((s) => (
          <span key={s.label} className="choro-key"><i style={{ background: s.color }} />{s.label}</span>
        ))}
        <span className="choro-progress">{Math.round(progress * 100)}% resolved</span>
      </div>

      <p className="sr-only">
        Journey recovery map. {regions.map((r) => `${r.name}: ${(STATUS[r.status] ?? STATUS.at_risk).label}.`).join(' ')}
        {' '}Overall {Math.round(progress * 100)} percent of the recovery is committed.
      </p>
    </div>
  );
}
