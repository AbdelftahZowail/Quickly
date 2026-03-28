"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.webhookFields = exports.webhookOperations = void 0;
const rlField_1 = require("./rlField");
exports.webhookOperations = [
    {
        name: 'List Event Types',
        value: 'listEventTypes',
        description: 'GET /api/settings/webhooks/events',
        action: 'Supported webhook event strings',
    },
    {
        name: 'Get Many',
        value: 'getAll',
        description: 'GET /api/settings/webhooks',
        action: 'List configured webhooks',
    },
    {
        name: 'Create',
        value: 'create',
        description: 'POST /api/settings/webhooks',
        action: 'Create a webhook',
    },
    {
        name: 'Update',
        value: 'update',
        description: 'PATCH /api/settings/webhooks/{id}',
        action: 'Update a webhook',
    },
    {
        name: 'Delete',
        value: 'delete',
        description: 'DELETE /api/settings/webhooks/{id}',
        action: 'Delete a webhook',
    },
    {
        name: 'Test',
        value: 'test',
        description: 'POST /api/settings/webhooks/{id}/test',
        action: 'Send test payload',
    },
];
exports.webhookFields = [
    (0, rlField_1.resourceLocatorField)('Webhook', 'webhookId', 'searchWebhooks', 'e.g. 1', {
        show: {
            resource: ['webhook'],
            operation: ['update', 'delete', 'test'],
        },
    }, true, 'From list or By ID'),
    {
        displayName: 'URL',
        name: 'webhookUrl',
        type: 'string',
        default: '',
        required: true,
        displayOptions: {
            show: {
                resource: ['webhook'],
                operation: ['create'],
            },
        },
    },
    {
        displayName: 'Events',
        name: 'webhookEvents',
        type: 'multiOptions',
        typeOptions: {
            loadOptionsMethod: 'getWebhookEventTypes',
        },
        allowArbitraryValues: true,
        default: [],
        required: true,
        displayOptions: {
            show: {
                resource: ['webhook'],
                operation: ['create'],
            },
        },
        description: 'Select event types and/or enter custom event name strings',
    },
    {
        displayName: 'Additional Fields',
        name: 'webhookCreateFields',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: {
            show: {
                resource: ['webhook'],
                operation: ['create'],
            },
        },
        options: [
            {
                displayName: 'Secret',
                name: 'secret',
                type: 'string',
                typeOptions: { password: true },
                default: '',
                description: 'Bearer token for Authorization on outbound POSTs',
            },
            {
                displayName: 'Active',
                name: 'active',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Description',
                name: 'description',
                type: 'string',
                default: '',
            },
        ],
    },
    {
        displayName: 'Update Fields',
        name: 'webhookUpdateFields',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: {
            show: {
                resource: ['webhook'],
                operation: ['update'],
            },
        },
        options: [
            {
                displayName: 'URL',
                name: 'url',
                type: 'string',
                default: '',
            },
            {
                displayName: 'Secret',
                name: 'secret',
                type: 'string',
                typeOptions: { password: true },
                default: '',
            },
            {
                displayName: 'Events',
                name: 'events',
                type: 'multiOptions',
                typeOptions: {
                    loadOptionsMethod: 'getWebhookEventTypes',
                },
                allowArbitraryValues: true,
                default: [],
            },
            {
                displayName: 'Active',
                name: 'active',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Description',
                name: 'description',
                type: 'string',
                default: '',
            },
        ],
    },
];
