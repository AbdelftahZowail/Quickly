import type {
  IDataObject,
  IExecuteFunctions,
  IHttpRequestMethods,
  ILoadOptionsFunctions,
} from 'n8n-workflow';
export function normalizeBaseUrl(raw: string): string {
  return String(raw || '').replace(/\/+$/, '');
}

export function getAuthHeaders(credentials: IDataObject): IDataObject {
  const auth = (credentials.authentication as string) || 'apiKey';
  if (auth === 'bearerToken') {
    const token = String(credentials.accessToken || '').trim();
    return { Authorization: `Bearer ${token}` };
  }
  return { 'X-API-Key': String(credentials.apiKey || '').trim() };
}

export type QuicklyRequestOptions = {
  method: IHttpRequestMethods;
  path: string;
  qs?: IDataObject;
  body?: unknown;
  /** When false, body is sent as-is (e.g. FormData). */
  json?: boolean;
  encoding?: 'json' | 'arraybuffer' | 'text';
};

export async function quicklyRequest(
  this: IExecuteFunctions | ILoadOptionsFunctions,
  credentials: IDataObject,
  opts: QuicklyRequestOptions,
): Promise<unknown> {
  const base = normalizeBaseUrl(String(credentials.baseUrl || ''));
  const path = opts.path.startsWith('/') ? opts.path : `/${opts.path}`;
  const url = `${base}${path}`;
  const headers: IDataObject = {
    Accept: opts.encoding === 'arraybuffer' ? '*/*' : 'application/json',
    ...getAuthHeaders(credentials),
  };

  const isForm =
    opts.body !== undefined &&
    typeof opts.body === 'object' &&
    opts.body !== null &&
    typeof (opts.body as { getHeaders?: () => IDataObject }).getHeaders === 'function';

  const json =
    !isForm &&
    opts.json !== false &&
    opts.encoding !== 'arraybuffer' &&
    opts.body !== undefined &&
    opts.method !== 'GET';

  if (json) {
    headers['Content-Type'] = 'application/json';
  }

  if (isForm) {
    const fd = opts.body as { getHeaders: () => IDataObject };
    Object.assign(headers, fd.getHeaders());
  }

  return await this.helpers.httpRequest({
    method: opts.method,
    url,
    headers,
    qs: opts.qs,
    body: opts.body as never,
    json: !isForm && opts.json !== false && opts.encoding !== 'arraybuffer',
    encoding: opts.encoding,
  });
}
