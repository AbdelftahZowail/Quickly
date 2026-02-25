const API_ROOT = '/api';

async function request(path, options = {}) {
  const res = await fetch(API_ROOT + path, options);
  if (!res.ok) {
    const text = await res.text();
    const err = new Error(text || res.statusText);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export const api = {
  get: (path) => request(path, { method: 'GET' }),
  post: (path, data) => request(path, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
  put: (path, data) => request(path, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
  patch: (path, data) => request(path, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) }),
  del: (path) => request(path, { method: 'DELETE' }),
};
