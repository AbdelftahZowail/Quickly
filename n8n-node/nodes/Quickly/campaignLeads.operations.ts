import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import {
  ENROLLMENT_STATUS_OPTIONS,
  INTEREST_ENROLLMENT_OPTIONS,
  INTEREST_FILTER_OPTIONS,
} from './constants';
import { resourceLocatorField } from './rlField';

export const campaignLeadOperations: INodePropertyOptions[] = [
  {
    name: 'Add Leads',
    value: 'add',
    description: 'POST /api/campaigns/{id}/leads',
    action: 'Bulk add or enroll leads',
  },
  {
    name: 'Get Many',
    value: 'getAll',
    description: 'GET /api/campaigns/{id}/leads',
    action: 'List enrolled leads',
  },
  {
    name: 'Remove from Campaign',
    value: 'remove',
    description: 'DELETE /api/campaigns/{id}/leads/{lead_id}',
    action: 'Unenroll a lead',
  },
  {
    name: 'Update Enrollment',
    value: 'updateEnrollment',
    description: 'PATCH /api/campaigns/{id}/leads/{lead_id}',
    action: 'Update status, interest, or pause',
  },
  {
    name: 'Detect Providers',
    value: 'detectProviders',
    description: 'POST /api/campaigns/{id}/leads/detect-providers',
    action: 'Queue DNS provider detection',
  },
  {
    name: 'Verify Emails (Campaign)',
    value: 'verifyEmails',
    description: 'POST /api/campaigns/{id}/leads/verify',
    action: 'Queue email verification',
  },
  {
    name: 'Verification Status Summary',
    value: 'verificationStatus',
    description: 'GET /api/campaigns/{id}/leads/verification-status',
    action: 'Counts by verification status',
  },
  {
    name: 'Export CSV',
    value: 'exportCsv',
    description: 'GET /api/campaigns/{id}/leads/export',
    action: 'Download campaign leads CSV',
  },
  {
    name: 'Import CSV',
    value: 'importCsv',
    description: 'POST /api/campaigns/{id}/leads/import',
    action: 'Upload a CSV of leads (multipart)',
  },
];

export const campaignLeadFields: INodeProperties[] = [
  resourceLocatorField(
    'Campaign',
    'campaignId',
    'searchCampaigns',
    'e.g. 42',
    { show: { resource: ['campaignLead'] } },
    true,
  ),
  {
    displayName: 'Leads JSON',
    name: 'leadsJson',
    type: 'string',
    typeOptions: {
      rows: 8,
    },
    default: '[\n  { "email": "alice@example.com", "name": "Alice" }\n]',
    required: true,
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['add'],
      },
    },
    description: 'JSON array of lead objects (see POST /api/campaigns/{id}/leads in API docs)',
  },
  {
    displayName: 'Skip Duplicates',
    name: 'skipDuplicates',
    type: 'boolean',
    default: true,
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['add', 'importCsv'],
      },
    },
  },
  {
    displayName: 'Verify Emails',
    name: 'verifyEmailsOnAdd',
    type: 'boolean',
    default: false,
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['add', 'importCsv'],
      },
    },
  },
  resourceLocatorField(
    'Lead',
    'campaignLeadId',
    'searchLeadsForCampaign',
    'e.g. 99',
    {
      show: {
        resource: ['campaignLead'],
        operation: ['remove', 'updateEnrollment'],
      },
    },
    true,
    'Leads enrolled in the selected campaign',
    undefined,
    ['campaignId'],
  ),
  {
    displayName: 'Enrollment Update Fields',
    name: 'enrollmentUpdateFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['updateEnrollment'],
      },
    },
    options: [
      {
        displayName: 'Status',
        name: 'status',
        type: 'options',
        options: ENROLLMENT_STATUS_OPTIONS,
        default: '',
      },
      {
        displayName: 'Interest',
        name: 'interest',
        type: 'options',
        options: INTEREST_ENROLLMENT_OPTIONS,
        default: '',
        description: 'Use Clear to send null',
      },
      {
        displayName: 'Sending Paused',
        name: 'sending_paused',
        type: 'boolean',
        default: false,
      },
    ],
  },
  {
    displayName: 'Verification Status',
    name: 'exportVerificationStatus',
    type: 'string',
    default: '',
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['exportCsv'],
      },
    },
    description: 'Optional filter (query: verification_status)',
  },
  {
    displayName: 'Enrollment Status',
    name: 'exportEnrollmentStatus',
    type: 'options',
    options: [{ name: '— All —', value: '__all__' }, ...ENROLLMENT_STATUS_OPTIONS],
    default: '__all__',
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['exportCsv'],
      },
    },
    description: 'Optional filter (query: status)',
  },
  {
    displayName: 'Interest',
    name: 'exportInterest',
    type: 'options',
    options: [{ name: '— All —', value: '__all__' }, ...INTEREST_FILTER_OPTIONS],
    default: '__all__',
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['exportCsv'],
      },
    },
    description: 'Optional filter (query: interest)',
  },
  {
    displayName: 'Input Binary Field',
    name: 'importBinaryProperty',
    type: 'string',
    default: 'data',
    required: true,
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['importCsv'],
      },
    },
    description: 'Name of the binary property on the input item containing the CSV file',
  },
  {
    displayName: 'File Name',
    name: 'importFileName',
    type: 'string',
    default: 'leads.csv',
    displayOptions: {
      show: {
        resource: ['campaignLead'],
        operation: ['importCsv'],
      },
    },
    description: 'Filename sent in multipart form',
  },
];
