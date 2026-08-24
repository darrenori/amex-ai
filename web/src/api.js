// Thin client for the FastAPI backend in api/index.py.
// In development Vite proxies /api to http://127.0.0.1:8000 (see vite.config.js).
// In production the backend runs as a Render web service: set VITE_API_BASE to
// its URL (e.g. https://tripshield-api.onrender.com/api) at build time. When it
// is unset the app falls back to the same-origin /api path (Vercel rewrite).

const BASE = import.meta.env.VITE_API_BASE || '/api';

// One session per browser tab. Execution runs are keyed off it server-side,
// because a run records transactions that actually happened and must not be
// reconstructed from seed data.
export const SESSION_ID = `s_${Math.random().toString(36).slice(2, 10)}`;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

async function request(path, { method = 'GET', body } = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    throw new ApiError('The TripShield service is unreachable. Check that the API is running.', 0);
  }

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const detail = payload && typeof payload.detail === 'string'
      ? payload.detail
      : `Request failed (${response.status}).`;
    throw new ApiError(detail, response.status);
  }

  return payload;
}

const q = (params) =>
  Object.entries(params)
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
    .join('&');

export const api = {
  health: () => request('/health'),
  login: (email, password) => request('/auth/login', { method: 'POST', body: { email, password } }),

  // Account
  account: () => request(`/account?${q({ session_id: SESSION_ID })}`),
  bookings: () => request(`/bookings?${q({ session_id: SESSION_ID })}`),
  benefits: () => request('/benefits'),
  profiles: () => request('/profiles'),
  connectors: () => request('/connectors'),

  // 1 · detection
  flightStatus: (flight, date) => request(`/flights/status?${q({ flight, date })}`),
  detect: () => request(`/disruption/detect?${q({ session_id: SESSION_ID })}`, { method: 'POST' }),

  // 2–3 · graph and impact
  graph: () => request(`/graph?${q({ session_id: SESSION_ID })}`),

  // 4–7 · planning
  plan: (priority = 'inferred', profileId = 'time') =>
    request('/recovery/plan', {
      method: 'POST',
      body: { session_id: SESSION_ID, priority, profile_id: profileId },
    }),
  rank: (priority, profileId = 'time') =>
    request(`/recovery/rank?${q({ session_id: SESSION_ID, priority, profile_id: profileId })}`),
  validate: (selections, { priority = 'inferred', profileId = 'time', basePlanId = null } = {}) =>
    request('/recovery/plan/validate', {
      method: 'POST',
      body: {
        session_id: SESSION_ID,
        selections,
        priority,
        profile_id: profileId,
        base_plan_id: basePlanId,
      },
    }),

  // 9–12 · approval, execution, compensation
  approve: (planId) =>
    request('/recovery/plan/approve', { method: 'POST', body: { session_id: SESSION_ID, plan_id: planId } }),
  run: (runId) => request(`/execution/${runId}?${q({ session_id: SESSION_ID })}`),
  advance: (runId, approvePayment = false) =>
    request(`/execution/${runId}/advance`, {
      method: 'POST',
      body: { session_id: SESSION_ID, approve_payment: approvePayment },
    }),
  rollbackQuote: (runId) => request(`/execution/${runId}/rollback-quote?${q({ session_id: SESSION_ID })}`),
  cancelRun: (runId, rollback = false) =>
    request(`/execution/${runId}/cancel`, {
      method: 'POST',
      body: { session_id: SESSION_ID, rollback },
    }),

  reset: () => request(`/session/reset?${q({ session_id: SESSION_ID })}`, { method: 'POST' }),
};
