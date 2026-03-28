import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import { INBOX_PROVIDER_OPTIONS, PAUSE_INBOX_ACTION_OPTIONS } from './constants';
import { resourceLocatorField } from './rlField';

export const inboxOperations: INodePropertyOptions[] = [
  {
    name: 'Get Many',
    value: 'getAll',
    description: 'GET /api/inboxes',
    action: 'List inboxes',
  },
  {
    name: 'Get',
    value: 'get',
    description: 'GET /api/inboxes/{id}',
    action: 'Get one inbox',
  },
  {
    name: 'Create',
    value: 'create',
    description: 'POST /api/inboxes',
    action: 'Create an inbox manually',
  },
  {
    name: 'Update',
    value: 'update',
    description: 'PATCH /api/inboxes/{id}',
    action: 'Update an inbox',
  },
  {
    name: 'Delete',
    value: 'delete',
    description: 'DELETE /api/inboxes/{id}',
    action: 'Delete an inbox',
  },
  {
    name: 'Pause',
    value: 'pause',
    description: 'POST /api/inboxes/{id}/pause',
    action: 'Pause sending',
  },
  {
    name: 'Unpause',
    value: 'unpause',
    description: 'POST /api/inboxes/{id}/unpause',
    action: 'Resume sending',
  },
];

export const inboxFields: INodeProperties[] = [
  resourceLocatorField(
    'Inbox',
    'inboxId',
    'searchInboxes',
    'e.g. 2',
    {
      show: {
        resource: ['inbox'],
        operation: ['get', 'update', 'delete', 'pause', 'unpause'],
      },
    },
    true,
  ),
  {
    displayName: 'Email',
    name: 'inboxEmail',
    type: 'string',
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['inbox'],
        operation: ['create'],
      },
    },
  },
  {
    displayName: 'Additional Fields',
    name: 'inboxCreateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['inbox'],
        operation: ['create'],
      },
    },
    options: [
      {
        displayName: 'Display Name',
        name: 'display_name',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Max Emails Per Day',
        name: 'max_emails_per_day',
        type: 'number',
        default: 50,
        typeOptions: { minValue: 1 },
      },
      {
        displayName: 'Wait Minutes Between',
        name: 'wait_minutes_between',
        type: 'number',
        default: 5,
        typeOptions: { minValue: 0 },
      },
      {
        displayName: 'Provider',
        name: 'provider',
        type: 'options',
        options: INBOX_PROVIDER_OPTIONS,
        default: 'gmail',
      },
      {
        displayName: 'Tracking Domain',
        name: 'tracking_domain',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Ramp Up Enabled',
        name: 'ramp_up_enabled',
        type: 'boolean',
        default: false,
      },
      {
        displayName: 'Ramp Up Period (Days)',
        name: 'ramp_up_period_days',
        type: 'number',
        default: 42,
        typeOptions: { minValue: 1 },
      },
      {
        displayName: 'Max Jitter Seconds',
        name: 'max_jitter_seconds',
        type: 'number',
        default: 180,
        typeOptions: { minValue: 0 },
      },
    ],
  },
  {
    displayName: 'Update Fields',
    name: 'inboxUpdateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['inbox'],
        operation: ['update'],
      },
    },
    options: [
      {
        displayName: 'Email',
        name: 'email',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Display Name',
        name: 'display_name',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Max Emails Per Day',
        name: 'max_emails_per_day',
        type: 'number',
        default: 50,
        typeOptions: { minValue: 1 },
      },
      {
        displayName: 'Wait Minutes Between',
        name: 'wait_minutes_between',
        type: 'number',
        default: 5,
        typeOptions: { minValue: 0 },
      },
      {
        displayName: 'Provider',
        name: 'provider',
        type: 'options',
        options: INBOX_PROVIDER_OPTIONS,
        default: 'gmail',
      },
      {
        displayName: 'Tracking Domain',
        name: 'tracking_domain',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Ramp Up Enabled',
        name: 'ramp_up_enabled',
        type: 'boolean',
        default: false,
      },
      {
        displayName: 'Ramp Up Period (Days)',
        name: 'ramp_up_period_days',
        type: 'number',
        default: 42,
        typeOptions: { minValue: 1 },
      },
      {
        displayName: 'Max Jitter Seconds',
        name: 'max_jitter_seconds',
        type: 'number',
        default: 180,
        typeOptions: { minValue: 0 },
      },
      {
        displayName: 'Paused',
        name: 'paused',
        type: 'boolean',
        default: false,
      },
    ],
  },
  {
    displayName: 'Pause Action',
    name: 'inboxPauseAction',
    type: 'options',
    options: PAUSE_INBOX_ACTION_OPTIONS,
    default: 'pause_leads',
    required: true,
    displayOptions: {
      show: {
        resource: ['inbox'],
        operation: ['pause'],
      },
    },
  },
];
