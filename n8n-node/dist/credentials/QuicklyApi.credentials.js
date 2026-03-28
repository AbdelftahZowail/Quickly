"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.QuicklyApi = void 0;
class QuicklyApi {
    constructor() {
        this.name = 'quicklyApi';
        this.displayName = 'Quickly API';
        this.icon = 'fa:paper-plane';
        this.documentationUrl = 'https://github.com/your-org/Quickly/blob/main/docs/API.md';
        /**
         * Programmatic auth avoids n8n expression bugs (regex in {{ }} caused "invalid syntax"
         * in the connection test) and sets either X-API-Key or Authorization correctly.
         */
        this.authenticate = async (credentials, requestOptions) => {
            var _a, _b, _c, _d;
            const baseURL = String((_a = credentials.baseUrl) !== null && _a !== void 0 ? _a : '').replace(/\/+$/, '');
            const auth = String((_b = credentials.authentication) !== null && _b !== void 0 ? _b : 'apiKey');
            const prev = requestOptions.headers && typeof requestOptions.headers === 'object'
                ? requestOptions.headers
                : {};
            const headers = { ...prev };
            if (auth === 'bearerToken') {
                delete headers['X-API-Key'];
                headers.Authorization = `Bearer ${String((_c = credentials.accessToken) !== null && _c !== void 0 ? _c : '').trim()}`;
            }
            else {
                delete headers.Authorization;
                headers['X-API-Key'] = String((_d = credentials.apiKey) !== null && _d !== void 0 ? _d : '').trim();
            }
            return {
                ...requestOptions,
                baseURL,
                headers,
            };
        };
        /** baseURL must not use regex inside {{ }} — n8n’s expression parser rejects it. */
        this.test = {
            request: {
                method: 'GET',
                baseURL: '={{$credentials.baseUrl}}',
                url: '/api/auth/me',
            },
        };
        this.properties = [
            {
                displayName: 'Base URL',
                name: 'baseUrl',
                type: 'string',
                default: 'http://localhost:8000',
                placeholder: 'https://your-quickly-host.com',
                description: 'Root URL of your Quickly instance (no trailing slash required)',
                required: true,
            },
            {
                displayName: 'Authentication',
                name: 'authentication',
                type: 'options',
                options: [
                    {
                        name: 'API Key',
                        value: 'apiKey',
                        description: 'Uses the X-API-Key header (recommended for automation)',
                    },
                    {
                        name: 'Bearer Token (JWT)',
                        value: 'bearerToken',
                        description: 'Uses Authorization: Bearer from POST /api/auth/login',
                    },
                ],
                default: 'apiKey',
            },
            {
                displayName: 'API Key',
                name: 'apiKey',
                type: 'string',
                typeOptions: {
                    password: true,
                },
                default: '',
                displayOptions: {
                    show: {
                        authentication: ['apiKey'],
                    },
                },
                description: 'Create under Settings → API Keys in Quickly',
            },
            {
                displayName: 'Access Token',
                name: 'accessToken',
                type: 'string',
                typeOptions: {
                    password: true,
                },
                default: '',
                displayOptions: {
                    show: {
                        authentication: ['bearerToken'],
                    },
                },
                description: 'JWT access_token from POST /api/auth/login (expires in 30 minutes by default)',
            },
        ];
    }
}
exports.QuicklyApi = QuicklyApi;
