"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.normalizeBaseUrl = normalizeBaseUrl;
exports.getAuthHeaders = getAuthHeaders;
exports.quicklyRequest = quicklyRequest;
function normalizeBaseUrl(raw) {
    return String(raw || '').replace(/\/+$/, '');
}
function getAuthHeaders(credentials) {
    const auth = credentials.authentication || 'apiKey';
    if (auth === 'bearerToken') {
        const token = String(credentials.accessToken || '').trim();
        return { Authorization: `Bearer ${token}` };
    }
    return { 'X-API-Key': String(credentials.apiKey || '').trim() };
}
async function quicklyRequest(credentials, opts) {
    const base = normalizeBaseUrl(String(credentials.baseUrl || ''));
    const path = opts.path.startsWith('/') ? opts.path : `/${opts.path}`;
    const url = `${base}${path}`;
    const headers = {
        Accept: opts.encoding === 'arraybuffer' ? '*/*' : 'application/json',
        ...getAuthHeaders(credentials),
    };
    const isForm = opts.body !== undefined &&
        typeof opts.body === 'object' &&
        opts.body !== null &&
        typeof opts.body.getHeaders === 'function';
    const json = !isForm &&
        opts.json !== false &&
        opts.encoding !== 'arraybuffer' &&
        opts.body !== undefined &&
        opts.method !== 'GET';
    if (json) {
        headers['Content-Type'] = 'application/json';
    }
    if (isForm) {
        const fd = opts.body;
        Object.assign(headers, fd.getHeaders());
    }
    return await this.helpers.httpRequest({
        method: opts.method,
        url,
        headers,
        qs: opts.qs,
        body: opts.body,
        json: !isForm && opts.json !== false && opts.encoding !== 'arraybuffer',
        encoding: opts.encoding,
    });
}
