"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.campaignFields = exports.campaignOperations = void 0;
const constants_1 = require("./constants");
const rlField_1 = require("./rlField");
exports.campaignOperations = [
    {
        name: 'Get Many',
        value: 'getAll',
        description: 'GET /api/campaigns',
        action: 'List all campaigns',
    },
    {
        name: 'Get',
        value: 'get',
        description: 'GET /api/campaigns/{id}',
        action: 'Get one campaign',
    },
    {
        name: 'Create',
        value: 'create',
        description: 'POST /api/campaigns',
        action: 'Create a campaign',
    },
    {
        name: 'Update',
        value: 'update',
        description: 'PATCH /api/campaigns/{id}',
        action: 'Update a campaign',
    },
    {
        name: 'Delete',
        value: 'delete',
        description: 'DELETE /api/campaigns/{id}',
        action: 'Delete a campaign',
    },
    {
        name: 'Duplicate',
        value: 'duplicate',
        description: 'POST /api/campaigns/{id}/duplicate',
        action: 'Duplicate a campaign',
    },
    {
        name: 'Reorder',
        value: 'reorder',
        description: 'POST /api/campaigns/reorder',
        action: 'Set campaign priority order',
    },
    {
        name: 'Has Leads (Any Campaign)',
        value: 'hasLeads',
        description: 'GET /api/campaigns/has-leads',
        action: 'Check if any campaign has enrolled leads',
    },
    {
        name: 'Get Queue',
        value: 'getQueue',
        description: 'GET /api/campaigns/{id}/queue',
        action: 'List pending queue slots',
    },
    {
        name: 'Get Sent',
        value: 'getSent',
        description: 'GET /api/campaigns/{id}/sent',
        action: 'Sent email history for the campaign',
    },
    {
        name: 'Recalculate Queue',
        value: 'recalculateQueue',
        description: 'POST /api/campaigns/{id}/recalculate-queue',
        action: 'Recalculate queue for one campaign',
    },
    {
        name: 'Get Analytics Steps',
        value: 'getAnalyticsSteps',
        description: 'GET /api/campaigns/{id}/analytics/steps',
        action: 'Per-step analytics including A/B variants',
    },
    {
        name: 'Preview Email',
        value: 'previewEmail',
        description: 'POST /api/campaigns/{id}/preview',
        action: 'Render a sequence (and optional lead) preview',
    },
    {
        name: 'Send Test Email',
        value: 'sendTestEmail',
        description: 'POST /api/campaigns/{id}/send-test',
        action: 'Send a real test email',
    },
];
const campaignIdField = (operations) => (0, rlField_1.resourceLocatorField)('Campaign', 'campaignId', 'searchCampaigns', 'e.g. 42', { show: { resource: ['campaign'], operation: operations } }, true, 'From List or By ID (numeric id or expression)');
exports.campaignFields = [
    campaignIdField(['get', 'update', 'delete', 'duplicate', 'getQueue', 'getSent', 'recalculateQueue', 'getAnalyticsSteps', 'previewEmail', 'sendTestEmail']),
    {
        displayName: 'Name',
        name: 'name',
        type: 'string',
        default: '',
        required: true,
        displayOptions: {
            show: {
                resource: ['campaign'],
                operation: ['create'],
            },
        },
    },
    {
        displayName: 'Inboxes',
        name: 'inboxIds',
        type: 'multiOptions',
        typeOptions: {
            loadOptionsMethod: 'getInboxes',
            loadOptionsDependsOn: ['resource', 'operation'],
        },
        allowArbitraryValues: true,
        default: [],
        required: true,
        displayOptions: {
            show: {
                resource: ['campaign'],
                operation: ['create'],
            },
        },
        description: 'Sending inboxes for this campaign (select or enter inbox IDs)',
    },
    {
        displayName: 'Additional Fields',
        name: 'additionalFields',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: {
            show: {
                resource: ['campaign'],
                operation: ['create'],
            },
        },
        options: [
            {
                displayName: 'Sending Days',
                name: 'sending_days',
                type: 'multiOptions',
                options: constants_1.WEEKDAY_OPTIONS,
                default: [],
                description: 'Weekdays to send (0=Mon … 6=Sun). Leave empty to use server default.',
            },
            {
                displayName: 'Sending Hours Start',
                name: 'sending_hours_start',
                type: 'string',
                default: '',
                placeholder: '09:00',
            },
            {
                displayName: 'Sending Hours End',
                name: 'sending_hours_end',
                type: 'string',
                default: '',
                placeholder: '17:00',
            },
            {
                displayName: 'Stop on Reply',
                name: 'stop_on_reply',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Paused',
                name: 'paused',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Priority',
                name: 'priority',
                type: 'number',
                default: 0,
                typeOptions: {
                    minValue: 0,
                },
                description: 'Lower number = higher priority (0 lets the server auto-assign)',
            },
            {
                displayName: 'Timezone',
                name: 'timezone',
                type: 'string',
                default: '',
                placeholder: 'America/New_York',
            },
            {
                displayName: 'Track Opens',
                name: 'track_opens',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Track Clicks',
                name: 'track_clicks',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Add Unsubscribe Header',
                name: 'add_unsubscribe_header',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Send First as Text',
                name: 'send_first_as_text',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Send All as Text',
                name: 'send_all_as_text',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Match Lead Provider',
                name: 'match_lead_provider',
                type: 'boolean',
                default: false,
            },
        ],
    },
    {
        displayName: 'Update Fields',
        name: 'updateFields',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: {
            show: {
                resource: ['campaign'],
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
                displayName: 'Inboxes',
                name: 'inbox_ids',
                type: 'multiOptions',
                typeOptions: {
                    loadOptionsMethod: 'getInboxes',
                    loadOptionsDependsOn: ['resource', 'operation'],
                },
                allowArbitraryValues: true,
                default: [],
            },
            {
                displayName: 'Sending Days',
                name: 'sending_days',
                type: 'multiOptions',
                options: constants_1.WEEKDAY_OPTIONS,
                default: [],
            },
            {
                displayName: 'Sending Hours Start',
                name: 'sending_hours_start',
                type: 'string',
                default: '',
                placeholder: '09:00',
            },
            {
                displayName: 'Sending Hours End',
                name: 'sending_hours_end',
                type: 'string',
                default: '',
                placeholder: '17:00',
            },
            {
                displayName: 'Stop on Reply',
                name: 'stop_on_reply',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Paused',
                name: 'paused',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Priority',
                name: 'priority',
                type: 'number',
                default: 0,
                typeOptions: { minValue: 0 },
            },
            {
                displayName: 'Timezone',
                name: 'timezone',
                type: 'string',
                default: '',
            },
            {
                displayName: 'Track Opens',
                name: 'track_opens',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Track Clicks',
                name: 'track_clicks',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Add Unsubscribe Header',
                name: 'add_unsubscribe_header',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Send First as Text',
                name: 'send_first_as_text',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Send All as Text',
                name: 'send_all_as_text',
                type: 'boolean',
                default: false,
            },
            {
                displayName: 'Match Lead Provider',
                name: 'match_lead_provider',
                type: 'boolean',
                default: false,
            },
        ],
    },
    {
        displayName: 'Campaign Order',
        name: 'campaignIdsOrdered',
        type: 'string',
        default: '[]',
        required: true,
        displayOptions: {
            show: {
                resource: ['campaign'],
                operation: ['reorder'],
            },
        },
        description: 'JSON array of campaign IDs in priority order, e.g. [3,1,2]. Sent as campaign_ids to POST /api/campaigns/reorder.',
    },
    (0, rlField_1.resourceLocatorField)('Sequence', 'sequenceId', 'searchSequencesForCampaign', 'e.g. 5', {
        show: {
            resource: ['campaign'],
            operation: ['previewEmail', 'sendTestEmail'],
        },
    }, true, 'Sequences for the selected campaign', undefined, ['campaignId']),
    (0, rlField_1.resourceLocatorField)('Lead', 'previewLeadId', 'searchLeadsForCampaign', 'e.g. 99', {
        show: {
            resource: ['campaign'],
            operation: ['previewEmail', 'sendTestEmail'],
        },
    }, false, 'Optional — leads enrolled in this campaign', undefined, ['campaignId']),
    (0, rlField_1.resourceLocatorField)('Variant', 'variantId', 'searchVariantsForSequence', 'e.g. 12 or __none__', {
        show: {
            resource: ['campaign'],
            operation: ['previewEmail', 'sendTestEmail'],
        },
    }, false, 'Optional — pick __none__ for default sequence content', undefined, ['campaignId', 'sequenceId']),
    {
        displayName: 'To Email',
        name: 'toEmail',
        type: 'string',
        default: '',
        required: true,
        displayOptions: {
            show: {
                resource: ['campaign'],
                operation: ['sendTestEmail'],
            },
        },
        description: 'Recipient for the test send',
    },
];
