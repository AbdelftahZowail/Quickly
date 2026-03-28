import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import { resourceLocatorField } from './rlField';

export const sequenceVariantOperations: INodePropertyOptions[] = [
  {
    name: 'Get Many',
    value: 'getAll',
    description: 'GET /api/campaigns/{id}/sequences/{seq_id}/variants',
    action: 'List A/B variants',
  },
  {
    name: 'Create',
    value: 'create',
    description: 'POST /api/campaigns/{id}/sequences/{seq_id}/variants',
    action: 'Create a variant',
  },
  {
    name: 'Update',
    value: 'update',
    description: 'PATCH /api/campaigns/{id}/sequences/{seq_id}/variants/{variant_id}',
    action: 'Update a variant',
  },
  {
    name: 'Delete',
    value: 'delete',
    description: 'DELETE /api/campaigns/{id}/sequences/{seq_id}/variants/{variant_id}',
    action: 'Delete a variant',
  },
];

export const sequenceVariantFields: INodeProperties[] = [
  resourceLocatorField(
    'Campaign',
    'campaignId',
    'searchCampaigns',
    'e.g. 42',
    { show: { resource: ['sequenceVariant'] } },
    true,
  ),
  resourceLocatorField(
    'Sequence',
    'sequenceId',
    'searchSequencesForCampaign',
    'e.g. 5',
    { show: { resource: ['sequenceVariant'] } },
    true,
  ),
  resourceLocatorField(
    'Variant',
    'variantId',
    'searchVariantsForSequence',
    'e.g. 7',
    { show: { resource: ['sequenceVariant'], operation: ['update', 'delete'] } },
    true,
    undefined,
    undefined,
    ['campaignId', 'sequenceId'],
  ),
  {
    displayName: 'Body',
    name: 'variantBody',
    type: 'string',
    typeOptions: {
      rows: 6,
    },
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['sequenceVariant'],
        operation: ['create'],
      },
    },
  },
  {
    displayName: 'Additional Fields',
    name: 'variantCreateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['sequenceVariant'],
        operation: ['create'],
      },
    },
    options: [
      {
        displayName: 'Label',
        name: 'label',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Subject',
        name: 'subject',
        type: 'string',
        default: '',
        description: 'Override subject; omit for sequence default',
      },
      {
        displayName: 'Is HTML',
        name: 'is_html',
        type: 'boolean',
        default: false,
        description: 'Override sequence HTML flag when set',
      },
      {
        displayName: 'Preview Text',
        name: 'preview_text',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Enabled',
        name: 'enabled',
        type: 'boolean',
        default: true,
      },
    ],
  },
  {
    displayName: 'Update Fields',
    name: 'variantUpdateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['sequenceVariant'],
        operation: ['update'],
      },
    },
    options: [
      {
        displayName: 'Label',
        name: 'label',
        type: 'string',
        default: '',
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
      {
        displayName: 'Enabled',
        name: 'enabled',
        type: 'boolean',
        default: true,
      },
    ],
  },
];
