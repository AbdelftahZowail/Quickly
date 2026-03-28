import type { INodeProperties, INodePropertyOptions } from 'n8n-workflow';
import { resourceLocatorField } from './rlField';

export const uniboxOperations: INodePropertyOptions[] = [
  {
    name: 'List Conversations',
    value: 'listConversations',
    description: 'GET /api/unibox',
    action: 'Paginated conversation list',
  },
  {
    name: 'Get Sync Status',
    value: 'getSyncStatus',
    description: 'GET /api/unibox/status',
    action: 'Sync status per inbox',
  },
  {
    name: 'Get Unread Count',
    value: 'getUnreadCount',
    description: 'GET /api/unibox/notifications',
    action: 'Unread lead-reply threads',
  },
  {
    name: 'Get Thread',
    value: 'getThread',
    description: 'GET /api/unibox/threads/{thread_id}',
    action: 'Messages in a thread',
  },
  {
    name: 'Mark Thread Read',
    value: 'markThreadRead',
    description: 'POST /api/unibox/threads/{thread_id}/mark-read',
    action: 'Clear unread flag',
  },
  {
    name: 'Sync',
    value: 'sync',
    description: 'POST /api/unibox/sync',
    action: 'Trigger Gmail sync',
  },
  {
    name: 'Load More (Backfill)',
    value: 'loadMore',
    description: 'POST /api/unibox/load-more',
    action: 'Load older messages',
  },
  {
    name: 'Send Email',
    value: 'sendEmail',
    description: 'POST /api/unibox/send',
    action: 'Send via Gmail from Unibox',
  },
];

export const uniboxFields: INodeProperties[] = [
  {
    displayName: 'Page',
    name: 'uniboxPage',
    type: 'number',
    default: 1,
    typeOptions: { minValue: 1 },
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['listConversations'],
      },
    },
  },
  {
    displayName: 'Page Size',
    name: 'uniboxPageSize',
    type: 'number',
    default: 20,
    typeOptions: { minValue: 1 },
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['listConversations'],
      },
    },
  },
  {
    displayName: 'Leads Only',
    name: 'uniboxLeadsOnly',
    type: 'boolean',
    default: false,
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['listConversations'],
      },
    },
  },
  resourceLocatorField(
    'Inbox',
    'uniboxFilterInboxId',
    'searchInboxes',
    'e.g. 2',
    {
      show: {
        resource: ['unibox'],
        operation: ['getSyncStatus'],
      },
    },
    false,
    'Optional filter (?inbox_id=)',
  ),
  resourceLocatorField(
    'Thread',
    'uniboxThreadId',
    'searchUniboxThreads',
    'e.g. 3||abc123',
    {
      show: {
        resource: ['unibox'],
        operation: ['getThread', 'markThreadRead'],
      },
    },
    true,
    'From list (inbox||thread) or By ID — paste inbox_id||thread_id',
  ),
  resourceLocatorField(
    'Inbox',
    'uniboxThreadInboxId',
    'searchInboxes',
    'e.g. 2',
    {
      show: {
        resource: ['unibox'],
        operation: ['getThread'],
      },
    },
    false,
    'Optional ?inbox_id= override',
  ),
  resourceLocatorField(
    'Inbox',
    'uniboxSyncInboxId',
    'searchInboxes',
    'e.g. 2',
    {
      show: {
        resource: ['unibox'],
        operation: ['sync', 'loadMore'],
      },
    },
    false,
    'Optional — omit for all inboxes',
  ),
  {
    displayName: 'Window Days',
    name: 'uniboxWindowDays',
    type: 'number',
    default: 30,
    typeOptions: { minValue: 1 },
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['loadMore'],
      },
    },
  },
  resourceLocatorField(
    'Inbox',
    'uniboxSendInboxId',
    'searchInboxes',
    'e.g. 2',
    {
      show: {
        resource: ['unibox'],
        operation: ['sendEmail'],
      },
    },
    true,
  ),
  {
    displayName: 'To Email',
    name: 'uniboxToEmail',
    type: 'string',
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['sendEmail'],
      },
    },
  },
  {
    displayName: 'Subject',
    name: 'uniboxSubject',
    type: 'string',
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['sendEmail'],
      },
    },
  },
  {
    displayName: 'Body',
    name: 'uniboxBody',
    type: 'string',
    typeOptions: { rows: 6 },
    default: '',
    required: true,
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['sendEmail'],
      },
    },
  },
  {
    displayName: 'Additional Fields',
    name: 'uniboxSendFields',
    type: 'collection',
    placeholder: 'Add Field',
    default: {},
    displayOptions: {
      show: {
        resource: ['unibox'],
        operation: ['sendEmail'],
      },
    },
    options: [
      {
        displayName: 'Thread ID',
        name: 'thread_id',
        type: 'string',
        default: '',
      },
      {
        displayName: 'In-Reply-To',
        name: 'in_reply_to',
        type: 'string',
        default: '',
      },
      {
        displayName: 'References',
        name: 'references',
        type: 'string',
        default: '',
      },
      {
        displayName: 'Is HTML',
        name: 'is_html',
        type: 'boolean',
        default: false,
      },
    ],
  },
];
