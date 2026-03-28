import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';

export const notificationOperations: INodePropertyOptions[] = [
  {
    name: 'Get Config',
    value: 'getConfig',
    description: 'GET /api/notifications/config',
    action: 'Read notification settings',
  },
  {
    name: 'Update Config',
    value: 'updateConfig',
    description: 'PUT /api/notifications/config',
    action: 'Save notification settings',
  },
];

export const notificationFields: INodeProperties[] = [
  {
    displayName: 'Enabled',
    name: 'notificationEnabled',
    type: 'boolean',
    default: true,
    required: true,
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['updateConfig'],
      },
    },
  },
  {
    displayName: 'Notification Email',
    name: 'notificationEmail',
    type: 'string',
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['updateConfig'],
      },
    },
  },
  {
    displayName: 'Events',
    name: 'notificationEvents',
    type: 'multiOptions',
    typeOptions: {
      loadOptionsMethod: 'getWebhookEventTypes',
    },
    allowArbitraryValues: true,
    default: [],
    required: true,
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['updateConfig'],
      },
    },
    description: 'Same event type strings as webhooks (select and/or type custom names)',
  },
  {
    displayName: 'Rate Limit Per Hour',
    name: 'notificationRateLimit',
    type: 'number',
    default: 10,
    required: true,
    typeOptions: { minValue: 1, maxValue: 100 },
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['updateConfig'],
      },
    },
  },
];
