const API_ROOT = '/api';

// In-memory stale-while-revalidate cache.
// Stores the last successful GET response for each path so pages can show
// instant data on re-visit while fresh data arrives in the background.
const _memCache = new Map();

export const apiCache = {
  /** Return the last successful response for the given path, or undefined. */
  get: (path) => _memCache.get(path),
};

// Import the in-memory token getter
import { getAccessToken, setAccessToken } from './context/AuthContext';

function _authHeaders() {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// Singleton promise for in-flight refresh – prevents multiple concurrent 401s
// from each independently trying to rotate the refresh token (which would cause
// all but the first to fail because token rotation invalidates the previous cookie).
let _refreshPromise = null;

async function _refreshAccessToken() {
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = fetch(API_ROOT + '/auth/refresh', {
    method: 'POST',
    credentials: 'include',
  }).then(async (res) => {
    if (!res.ok) throw new Error('refresh failed');
    const data = await res.json();
    setAccessToken(data.access_token);
    return data.access_token;
  }).finally(() => {
    _refreshPromise = null;
  });
  return _refreshPromise;
}

async function request(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = { ..._authHeaders(), ...options.headers };
  // Force bypass of browser HTTP cache for GET requests so the app always
  // fetches fresh data from the server instead of serving a stale cached copy.
  const fetchOptions = method === 'GET'
    ? { ...options, headers, cache: 'no-store' }
    : { ...options, headers };
  const res = await fetch(API_ROOT + path, fetchOptions);
  if (res.status === 401) {
    // Try to refresh once (all concurrent 401s share the same refresh attempt).
    try {
      const newToken = await _refreshAccessToken();
      // Retry the original request with new token
      const retryHeaders = { Authorization: `Bearer ${newToken}`, ...options.headers };
      const retryOptions = method === 'GET'
        ? { ...options, headers: retryHeaders, cache: 'no-store' }
        : { ...options, headers: retryHeaders };
      const retryRes = await fetch(API_ROOT + path, retryOptions);
      if (!retryRes.ok) {
        const text = await retryRes.text();
        const err = new Error(text || retryRes.statusText);
        err.status = retryRes.status;
        throw err;
      }
      const retryData = await retryRes.json();
      if (method === 'GET') _memCache.set(path, retryData);
      return retryData;
    } catch {
      // Refresh failed – redirect to login
      window.location.href = '/login';
      throw new Error('Session expired');
    }
  }
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(text || res.statusText);
    err.status = res.status;
    throw err;
  }
  const data = await res.json();
  if (method === 'GET') _memCache.set(path, data);
  return data;
}

export const api = {
  get: (path) => request(path, { method: 'GET' }),
  post: (path, data) => request(path, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
  put: (path, data) => request(path, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
  patch: (path, data) => request(path, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
  del: (path) => request(path, { method: 'DELETE' }),
  upload: async (path, file) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(API_ROOT + path, { method: 'POST', body: form, headers: _authHeaders() });
    if (res.status === 401) {
      try {
        const newToken = await _refreshAccessToken();
        const retryRes = await fetch(API_ROOT + path, {
          method: 'POST', body: form, headers: { Authorization: `Bearer ${newToken}` },
        });
        if (!retryRes.ok) {
          const text = await retryRes.text();
          const err = new Error(text || retryRes.statusText);
          err.status = retryRes.status;
          throw err;
        }
        return retryRes.json();
      } catch {
        window.location.href = '/login';
        throw new Error('Session expired');
      }
    }
    if (!res.ok) {
      const text = await res.text();
      const err = new Error(text || res.statusText);
      err.status = res.status;
      throw err;
    }
    return res.json();
  },
  download: (path) => fetch(API_ROOT + path, { method: 'GET', headers: _authHeaders() }),
};
