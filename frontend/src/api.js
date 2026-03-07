const API_ROOT = '/api';

// In-memory stale-while-revalidate cache.
// Stores the last successful GET response for each path so pages can show
// instant data on re-visit while fresh data arrives in the background.
const _memCache = new Map();

export const apiCache = {
  /** Return the last successful response for the given path, or undefined. */
  get: (path) => _memCache.get(path),
};

async function request(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  // Force bypass of browser HTTP cache for GET requests so the app always
  // fetches fresh data from the server instead of serving a stale cached copy.
  const fetchOptions = method === 'GET'
    ? { ...options, cache: 'no-store' }
    : options;
  const res = await fetch(API_ROOT + path, fetchOptions);
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
    const res = await fetch(API_ROOT + path, { method: 'POST', body: form });
    if (!res.ok) {
      const text = await res.text();
      const err = new Error(text || res.statusText);
      err.status = res.status;
      throw err;
    }
    return res.json();
  },
  download: (path) => fetch(API_ROOT + path, { method: 'GET' }),
};
