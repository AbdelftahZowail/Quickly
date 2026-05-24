import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';

export const notificationOperations: INodePropertyOptions[] = [
  {
    name: 'List',
    value: 'list',
    description: 'GET /api/notifications',
    action: 'List notifications',
  },
  {
    name: 'Mark Read',
    value: 'markRead',
    description: 'PATCH /api/notifications/{id}/read',
    action: 'Mark a notification as read',
  },
  {
    name: 'Mark All Read',
    value: 'markAllRead',
    description: 'POST /api/notifications/read-all',
    action: 'Mark all notifications as read',
  },
  {
    name: 'Delete',
    value: 'delete',
    description: 'DELETE /api/notifications/{id}',
    action: 'Delete a notification',
  },
  {
    name: 'Unread Count',
    value: 'unreadCount',
    description: 'GET /api/notifications/unread-count',
    action: 'Get unread notification count',
  },
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
    displayName: 'Unread Only',
    name: 'unreadOnly',
    type: 'boolean',
    default: false,
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['list'],
      },
    },
  },
  {
    displayName: 'Limit',
    name: 'limit',
    type: 'number',
    default: 50,
    typeOptions: { minValue: 1, maxValue: 200 },
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['list'],
      },
    },
  },
  {
    displayName: 'Notification ID',
    name: 'notificationId',
    type: 'number',
    default: 0,
    required: true,
    displayOptions: {
      show: {
        resource: ['notification'],
        operation: ['markRead', 'delete'],
      },
    },
  },
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
