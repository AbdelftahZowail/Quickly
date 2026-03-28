import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';

export const statusOperations: INodePropertyOptions[] = [
  {
    name: 'Get App Status',
    value: 'getStatus',
    description: 'GET /api/status',
    action: 'Scheduler and health banner data',
  },
  {
    name: 'Get System Health',
    value: 'getSystemHealth',
    description: 'GET /api/system-health',
    action: 'OAuth, inboxes, unibox, AI, verification snapshot',
  },
];

export const statusFields: INodeProperties[] = [];
