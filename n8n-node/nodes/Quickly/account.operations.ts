import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';

export const accountOperations: INodePropertyOptions[] = [
  {
    name: 'Get Current User',
    value: 'getMe',
    description: 'GET /api/auth/me',
    action: 'Get the authenticated user',
  },
];

export const accountFields: INodeProperties[] = [];
