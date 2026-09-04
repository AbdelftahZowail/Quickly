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

/** Set by server on successful POST /settings/backup/restore/execute so the SPA reloads with fresh data. */
const RELOAD_AFTER_RESTORE_HEADER = 'X-Quickly-Reload';

function scheduleReloadIfRestoreComplete(res) {
  if (res.headers.get(RELOAD_AFTER_RESTORE_HEADER) === '1') {
    setTimeout(() => window.location.reload(), 300);
  }
}

/**
 * Redirect to /login on session expiry – but never when already there.
 * Without the pathname guard, any authenticated poll mounted on the login
 * page (e.g. NotificationsProvider) would 401 → redirect → full reload →
 * re-mount → 401 … an infinite reload loop.
 */
function _redirectToLoginIfNeeded() {
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
}

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
      scheduleReloadIfRestoreComplete(retryRes);
      if (method === 'GET') _memCache.set(path, retryData);
      return retryData;
    } catch {
      // Refresh failed – redirect to login (unless already there – see
      // _redirectToLoginIfNeeded – otherwise the login page reload-loops).
      _redirectToLoginIfNeeded();
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
  scheduleReloadIfRestoreComplete(res);
  if (method === 'GET') _memCache.set(path, data);
  return data;
}

/** GET with auth + refresh; returns raw Response (for CSV, etc.). */
async function downloadRequest(path) {
  const headers = { ..._authHeaders() };
  let res = await fetch(API_ROOT + path, { method: 'GET', headers, cache: 'no-store' });
  if (res.status === 401) {
    try {
      const newToken = await _refreshAccessToken();
      res = await fetch(API_ROOT + path, {
        method: 'GET',
        headers: { Authorization: `Bearer ${newToken}` },
        cache: 'no-store',
      });
      if (!res.ok) {
        const text = await res.text();
        const err = new Error(text || res.statusText);
        err.status = res.status;
        throw err;
      }
      return res;
    } catch (e) {
      if (e.status) throw e;
      _redirectToLoginIfNeeded();
      throw new Error('Session expired');
    }
  }
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(text || res.statusText);
    err.status = res.status;
    throw err;
  }
  return res;
}


/** POST that returns a blob (e.g. encrypted backup download). */
async function downloadPostRequest(path, data) {
  const headers = { ..._authHeaders(), 'Content-Type': 'application/json' };
  let res = await fetch(API_ROOT + path, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
    cache: 'no-store',
  });
  if (res.status === 401) {
    try {
      const newToken = await _refreshAccessToken();
      res = await fetch(API_ROOT + path, {
        method: 'POST',
        headers: { Authorization: `Bearer ${newToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
        cache: 'no-store',
      });
      if (!res.ok) {
        const text = await res.text();
        const err = new Error(text || res.statusText);
        err.status = res.status;
        throw err;
      }
      return res;
    } catch (e) {
      if (e.status) throw e;
      _redirectToLoginIfNeeded();
      throw new Error('Session expired');
    }
  }
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(text || res.statusText);
    err.status = res.status;
    throw err;
  }
  return res;
}

/** POST JSON, return raw Response (e.g. file download). Named export avoids stale `api` bundles missing `downloadPost`. */
export async function postJsonForDownload(path, data) {
  return downloadPostRequest(path, data);
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
        _redirectToLoginIfNeeded();
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
  download: (path) => downloadRequest(path),
  downloadPost: (path, data) => postJsonForDownload(path, data),
  /** Multipart upload with optional password field (restore preview). */
  uploadMultipart: async (path, file, fields = {}) => {
    const form = new FormData();
    form.append('file', file);
    if (fields.password != null && fields.password !== '') {
      form.append('password', fields.password);
    }
    const res = await fetch(API_ROOT + path, { method: 'POST', body: form, headers: _authHeaders() });
    if (res.status === 401) {
      try {
        const newToken = await _refreshAccessToken();
        const retryRes = await fetch(API_ROOT + path, {
          method: 'POST',
          body: form,
          headers: { Authorization: `Bearer ${newToken}` },
        });
        if (!retryRes.ok) {
          const text = await retryRes.text();
          const err = new Error(text || retryRes.statusText);
          err.status = retryRes.status;
          throw err;
        }
        return retryRes.json();
      } catch {
        _redirectToLoginIfNeeded();
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
};
