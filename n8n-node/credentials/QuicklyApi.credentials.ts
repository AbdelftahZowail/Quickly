import type {
  ICredentialDataDecryptedObject,
  ICredentialType,
  IDataObject,
  INodeProperties,
  Icon,
  ICredentialTestRequest,
  IHttpRequestOptions,
  IAuthenticate,
} from 'n8n-workflow';

export class QuicklyApi implements ICredentialType {
  name = 'quicklyApi';

  displayName = 'Quickly API';

  icon: Icon = 'fa:paper-plane';

  documentationUrl = 'https://github.com/your-org/Quickly/blob/main/docs/API.md';

  /**
   * Programmatic auth avoids n8n expression bugs (regex in {{ }} caused "invalid syntax"
   * in the connection test) and sets either X-API-Key or Authorization correctly.
   */
  authenticate: IAuthenticate = async (
    credentials: ICredentialDataDecryptedObject,
    requestOptions: IHttpRequestOptions,
  ): Promise<IHttpRequestOptions> => {
    const baseURL = String(credentials.baseUrl ?? '').replace(/\/+$/, '');
    const auth = String(credentials.authentication ?? 'apiKey');
    const prev =
      requestOptions.headers && typeof requestOptions.headers === 'object'
        ? (requestOptions.headers as IDataObject)
        : {};
    const headers: IDataObject = { ...prev };
    if (auth === 'bearerToken') {
      delete headers['X-API-Key'];
      headers.Authorization = `Bearer ${String(credentials.accessToken ?? '').trim()}`;
    } else {
      delete headers.Authorization;
      headers['X-API-Key'] = String(credentials.apiKey ?? '').trim();
    }
    return {
      ...requestOptions,
      baseURL,
      headers,
    };
  };

  /** baseURL must not use regex inside {{ }} — n8n’s expression parser rejects it. */
  test: ICredentialTestRequest = {
    request: {
      method: 'GET',
      baseURL: '={{$credentials.baseUrl}}',
      url: '/api/auth/me',
    },
  };

  properties: INodeProperties[] = [
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
