// Thin client for the FastAPI backend in api/index.py.
// In development Vite proxies /api to http://127.0.0.1:8000 (see vite.config.js);
// in production Vercel rewrites /api/* onto the Python serverless function.

const BASE = '/api';

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

export const api = {
  health: () => request('/health'),
  login: (email, password) => request('/auth/login', { method: 'POST', body: { email, password } }),
  account: () => request('/account'),
  benefits: () => request('/benefits'),
  profiles: () => request('/profiles'),
  getTrip: (tripId = 'TRIP-001') => request(`/trips/${encodeURIComponent(tripId)}`),
  simulateCancellation: (tripId = 'TRIP-001') =>
    request('/disruptions', { method: 'POST', body: { trip_id: tripId, type: 'flight_cancelled' } }),
  getRecommendations: (tripId = 'TRIP-001', profileId = 'time') =>
    request('/recommendations', { method: 'POST', body: { trip_id: tripId, profile_id: profileId } }),
  confirmRecovery: (tripId = 'TRIP-001', candidateId, profileId = 'time') =>
    request('/recovery/confirm', {
      method: 'POST',
      body: { trip_id: tripId, candidate_id: candidateId, profile_id: profileId },
    }),
  simulate: (profileId = 'time') =>
    request(`/disruption/simulate?profile_id=${encodeURIComponent(profileId)}`, { method: 'POST' }),
  recovery: (profileId = 'time') => request(`/recovery?profile_id=${encodeURIComponent(profileId)}`),
  hold: (planId) => request('/recovery/hold', { method: 'POST', body: { plan_id: planId } }),
  confirm: (planId, profileId, emergency = false) =>
    request('/recovery/confirm', {
      method: 'POST',
      body: { plan_id: planId, profile_id: profileId, emergency },
    }),
};
