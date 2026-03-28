import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import {
  ENROLLMENT_STATUS_OPTIONS,
  INTEREST_FILTER_OPTIONS,
  LEAD_LIST_STATUS_OPTIONS,
} from './constants';
import { resourceLocatorField } from './rlField';

export const leadOperations: INodePropertyOptions[] = [
  {
    name: 'Get Many',
    value: 'getAll',
    description: 'GET /api/leads',
    action: 'List leads with optional filters',
  },
  {
    name: 'Get',
    value: 'get',
    description: 'GET /api/leads/{id}',
    action: 'Get one lead',
  },
  {
    name: 'Update',
    value: 'update',
    description: 'PATCH /api/leads/{id}',
    action: 'Update a lead',
  },
  {
    name: 'Delete',
    value: 'delete',
    description: 'DELETE /api/leads/{id}',
    action: 'Delete a lead',
  },
  {
    name: 'Bulk Delete',
    value: 'bulkDelete',
    description: 'POST /api/leads/bulk-delete',
    action: 'Delete many leads',
  },
  {
    name: 'Bulk Set Enrollment Status',
    value: 'bulkStatus',
    description: 'POST /api/leads/bulk-status',
    action: 'Set enrollment status on all enrollments',
  },
  {
    name: 'Bulk Recover',
    value: 'bulkRecover',
    description: 'POST /api/leads/bulk-recover',
    action: 'Fix emails and reset enrollments',
  },
  {
    name: 'Mark Replied',
    value: 'markReplied',
    description: 'POST /api/leads/mark-replied',
    action: 'Mark lead replied for a campaign',
  },
  {
    name: 'Recover (Single)',
    value: 'recover',
    description: 'POST /api/leads/{id}/recover',
    action: 'Recover one lead with new email',
  },
  {
    name: 'Get History',
    value: 'getHistory',
    description: 'GET /api/leads/{id}/history',
    action: 'Email send history',
  },
  {
    name: 'Get Reply Markers',
    value: 'getReplies',
    description: 'GET /api/leads/{id}/replies',
    action: 'Reply markers per campaign',
  },
  {
    name: 'Export CSV',
    value: 'exportCsv',
    description: 'GET /api/leads/export',
    action: 'Download leads as CSV (binary)',
  },
];

export const leadFields: INodeProperties[] = [
  {
    displayName: 'Search Query',
    name: 'leadSearchQuery',
    type: 'string',
    default: '',
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['getAll', 'exportCsv'],
      },
    },
    description: 'Substring match on email or name (q)',
  },
  {
    displayName: 'Status',
    name: 'leadFilterStatus',
    type: 'options',
    options: [{ name: '— All —', value: '__all__' }, ...LEAD_LIST_STATUS_OPTIONS],
    default: '__all__',
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['getAll', 'exportCsv'],
      },
    },
    description: 'Enrollment / special filters (see API docs)',
  },
  {
    displayName: 'Bad Only',
    name: 'leadBadOnly',
    type: 'boolean',
    default: false,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['getAll', 'exportCsv'],
      },
    },
  },
  {
    displayName: 'Interest',
    name: 'leadFilterInterest',
    type: 'options',
    options: [{ name: '— All —', value: '__all__' }, ...INTEREST_FILTER_OPTIONS],
    default: '__all__',
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['getAll', 'exportCsv'],
      },
    },
    description: 'Per-enrollment interest filter',
  },
  resourceLocatorField(
    'Lead',
    'leadId',
    'searchLeads',
    'e.g. 99',
    {
      show: {
        resource: ['lead'],
        operation: ['get', 'update', 'delete', 'recover', 'getHistory', 'getReplies'],
      },
    },
    true,
  ),
  {
    displayName: 'Leads',
    name: 'leadIds',
    type: 'multiOptions',
    typeOptions: {
      loadOptionsMethod: 'getLeads',
      loadOptionsDependsOn: ['resource', 'operation'],
    },
    allowArbitraryValues: true,
    default: [],
    required: true,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['bulkDelete', 'bulkStatus'],
      },
    },
    description: 'Select leads and/or type numeric lead IDs',
  },
  {
    displayName: 'Enrollment Status',
    name: 'enrollmentStatus',
    type: 'options',
    options: ENROLLMENT_STATUS_OPTIONS,
    default: 'active',
    required: true,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['bulkStatus'],
      },
    },
  },
  {
    displayName: 'Recovery Items',
    name: 'bulkRecoverItems',
    type: 'fixedCollection',
    typeOptions: {
      multipleValues: true,
    },
    default: {},
    required: true,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['bulkRecover'],
      },
    },
    options: [
      {
        displayName: 'Item',
        name: 'item',
        values: [
          resourceLocatorField('Lead', 'lead_id', 'searchLeads', 'e.g. 99', {}, true),
          {
            displayName: 'New Email',
            name: 'email',
            type: 'string',
            default: '',
            required: true,
          },
        ],
      },
    ],
    description: 'Each row: lead ID and new email address (lead ID: select or type)',
  },
  {
    displayName: 'Verify Email',
    name: 'verifyEmailAfterRecover',
    type: 'boolean',
    default: true,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['bulkRecover'],
      },
    },
  },
  resourceLocatorField(
    'Lead',
    'markRepliedLeadId',
    'searchLeads',
    'e.g. 99',
    { show: { resource: ['lead'], operation: ['markReplied'] } },
    true,
  ),
  resourceLocatorField(
    'Campaign',
    'markRepliedCampaignId',
    'searchCampaigns',
    'e.g. 42',
    { show: { resource: ['lead'], operation: ['markReplied'] } },
    true,
  ),
  {
    displayName: 'New Email',
    name: 'recoverEmail',
    type: 'string',
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['recover'],
      },
    },
  },
  {
    displayName: 'Verify Email',
    name: 'recoverVerifyEmail',
    type: 'boolean',
    default: true,
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['recover'],
      },
    },
  },
  {
    displayName: 'Update Fields',
    name: 'leadUpdateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['lead'],
        operation: ['update'],
      },
    },
    options: [
      {
        displayName: 'Name',
        name: 'name',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Custom Data (JSON)',
        name: 'custom_data_json',
        type: 'string',
        typeOptions: {
          rows: 4,
        },
        default: '',
        description: 'Object merged as custom_data (parse JSON)',
      },
      {
        displayName: 'Enrollment Status (all enrollments)',
        name: 'enrollment_status',
        type: 'options',
        options: ENROLLMENT_STATUS_OPTIONS,
        default: '',
        description: 'Leave empty to skip',
      },
    ],
  },
];
