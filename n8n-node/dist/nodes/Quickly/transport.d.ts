import type { IDataObject, IExecuteFunctions, IHttpRequestMethods, ILoadOptionsFunctions } from 'n8n-workflow';
export declare function normalizeBaseUrl(raw: string): string;
export declare function getAuthHeaders(credentials: IDataObject): IDataObject;
export type QuicklyRequestOptions = {
    method: IHttpRequestMethods;
    path: string;
    qs?: IDataObject;
    body?: unknown;
    /** When false, body is sent as-is (e.g. FormData). */
    json?: boolean;
    encoding?: 'json' | 'arraybuffer' | 'text';
};
export declare function quicklyRequest(this: IExecuteFunctions | ILoadOptionsFunctions, credentials: IDataObject, opts: QuicklyRequestOptions): Promise<unknown>;
