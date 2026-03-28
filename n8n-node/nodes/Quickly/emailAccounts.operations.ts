import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import { resourceLocatorField } from './rlField';

export const emailAccountOperations: INodePropertyOptions[] = [
  {
    name: 'Gmail OAuth Status',
    value: 'gmailStatus',
    description: 'GET /api/gmail/status',
    action: 'Whether Gmail OAuth is configured',
  },
  {
    name: 'List Gmail Accounts',
    value: 'gmailListAccounts',
    description: 'GET /api/gmail/accounts',
    action: 'Connected Gmail accounts',
  },
  {
    name: 'Disconnect Gmail Account',
    value: 'gmailDisconnect',
    description: 'DELETE /api/gmail/accounts/{id}',
    action: 'Remove tokens for a Gmail account',
  },
  {
    name: 'Gmail Permissions',
    value: 'gmailPermissions',
    description: 'GET /api/gmail/permissions',
    action: 'OAuth scopes per account',
  },
  {
    name: 'Office 365 OAuth Status',
    value: 'office365Status',
    description: 'GET /api/office365/status',
    action: 'Whether Microsoft OAuth is configured',
  },
  {
    name: 'List Office 365 Accounts',
    value: 'office365ListAccounts',
    description: 'GET /api/office365/accounts',
    action: 'Connected Microsoft accounts',
  },
  {
    name: 'Disconnect Office 365 Account',
    value: 'office365Disconnect',
    description: 'DELETE /api/office365/accounts/{id}',
    action: 'Remove tokens for a Microsoft account',
  },
];

export const emailAccountFields: INodeProperties[] = [
  resourceLocatorField(
    'Gmail Account',
    'gmailAccountId',
    'searchGmailAccounts',
    'e.g. 1',
    {
      show: {
        resource: ['emailAccount'],
        operation: ['gmailDisconnect'],
      },
    },
    true,
  ),
  resourceLocatorField(
    'Office 365 Account',
    'office365AccountId',
    'searchOffice365Accounts',
    'e.g. 1',
    {
      show: {
        resource: ['emailAccount'],
        operation: ['office365Disconnect'],
      },
    },
    true,
  ),
];
