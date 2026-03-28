import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import { resourceLocatorField } from './rlField';

export const sequenceOperations: INodePropertyOptions[] = [
  {
    name: 'Get Many',
    value: 'getAll',
    description: 'GET /api/campaigns/{id}/sequences',
    action: 'List sequences for a campaign',
  },
  {
    name: 'Create',
    value: 'create',
    description: 'POST /api/campaigns/{id}/sequences',
    action: 'Add a sequence step',
  },
  {
    name: 'Update',
    value: 'update',
    description: 'PATCH /api/campaigns/{id}/sequences/{seq_id}',
    action: 'Update a sequence',
  },
  {
    name: 'Delete',
    value: 'delete',
    description: 'DELETE /api/campaigns/{id}/sequences/{seq_id}',
    action: 'Delete a sequence step',
  },
];

export const sequenceFields: INodeProperties[] = [
  resourceLocatorField(
    'Campaign',
    'campaignId',
    'searchCampaigns',
    'e.g. 42',
    { show: { resource: ['sequence'] } },
    true,
  ),
  resourceLocatorField(
    'Sequence',
    'sequenceId',
    'searchSequencesForCampaign',
    'e.g. 5',
    { show: { resource: ['sequence'], operation: ['update', 'delete'] } },
    true,
    undefined,
    undefined,
    ['campaignId'],
  ),
  {
    displayName: 'Position',
    name: 'position',
    type: 'number',
    default: 1,
    required: true,
    typeOptions: {
      minValue: 1,
    },
    displayOptions: {
      show: {
        resource: ['sequence'],
        operation: ['create'],
      },
    },
    description: '1-based step number',
  },
  {
    displayName: 'Body',
    name: 'body',
    type: 'string',
    typeOptions: {
      rows: 6,
    },
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['sequence'],
        operation: ['create'],
      },
    },
  },
  {
    displayName: 'Wait Days After Previous',
    name: 'wait_days_after_previous',
    type: 'number',
    default: 0,
    required: true,
    typeOptions: { minValue: 0 },
    displayOptions: {
      show: {
        resource: ['sequence'],
        operation: ['create'],
      },
    },
  },
  {
    displayName: 'Additional Fields',
    name: 'sequenceCreateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['sequence'],
        operation: ['create'],
      },
    },
    options: [
      {
        displayName: 'Subject',
        name: 'subject',
        type: 'string',
        default: '',
        description: 'Empty = reply in thread',
      },
      {
        displayName: 'Is HTML',
        name: 'is_html',
        type: 'boolean',
        default: false,
      },
      {
        displayName: 'Preview Text',
        name: 'preview_text',
        type: 'string',
        default: '',
      },
    ],
  },
  {
    displayName: 'Update Fields',
    name: 'sequenceUpdateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['sequence'],
        operation: ['update'],
      },
    },
    options: [
      {
        displayName: 'Position',
        name: 'position',
        type: 'number',
        default: 1,
        typeOptions: { minValue: 1 },
      },
      {
        displayName: 'Subject',
        name: 'subject',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Body',
        name: 'body',
        type: 'string',
        typeOptions: { rows: 6 },
        default: '',
      },
      {
        displayName: 'Wait Days After Previous',
        name: 'wait_days_after_previous',
        type: 'number',
        default: 0,
        typeOptions: { minValue: 0 },
      },
      {
        displayName: 'Is HTML',
        name: 'is_html',
        type: 'boolean',
        default: false,
      },
      {
        displayName: 'Preview Text',
        name: 'preview_text',
        type: 'string',
        default: '',
      },
    ],
  },
];
