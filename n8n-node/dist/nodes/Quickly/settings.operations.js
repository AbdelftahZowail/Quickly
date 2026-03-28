"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.settingsFields = exports.settingsOperations = void 0;
const constants_1 = require("./constants");
const rlField_1 = require("./rlField");
exports.settingsOperations = [
    {
        name: 'Get Scheduling Strategy',
        value: 'getSchedulingStrategy',
        description: 'GET /api/settings/scheduling-strategy',
        action: 'Read priority vs round robin',
    },
    {
        name: 'Set Scheduling Strategy',
        value: 'setSchedulingStrategy',
        description: 'POST /api/settings/scheduling-strategy',
        action: 'Update scheduling strategy',
    },
    {
        name: 'Get Time Offset',
        value: 'getTimeOffset',
        description: 'GET /api/settings/time-offset',
        action: 'Simulation offset in days',
    },
    {
        name: 'Set Time Offset',
        value: 'setTimeOffset',
        description: 'POST /api/settings/time-offset',
        action: 'Set time offset days',
    },
    {
        name: 'Get Test Mode',
        value: 'getTestMode',
        description: 'GET /api/settings/test-mode',
        action: 'Whether test mode is enabled',
    },
    {
        name: 'Set Test Mode',
        value: 'setTestMode',
        description: 'POST /api/settings/test-mode',
        action: 'Enable or disable test mode',
    },
    {
        name: 'Get Server Info',
        value: 'getServerInfo',
        description: 'GET /api/settings/server-info',
        action: 'Base URL and CNAME target',
    },
    {
        name: 'Verify Tracking Domain',
        value: 'verifyTrackingDomain',
        description: 'GET /api/settings/verify-tracking-domain',
        action: 'DNS check for custom tracking domain',
    },
    {
        name: 'Get MCP Setup',
        value: 'getMcpSetup',
        description: 'GET /api/settings/mcp-setup',
        action: 'MCP HTTP URL and Cursor fragment',
    },
    {
        name: 'List Known IPs',
        value: 'listKnownIPs',
        description: 'GET /api/settings/known-ips',
        action: 'Known IPs and current client IP',
    },
    {
        name: 'Add Known IP',
        value: 'addKnownIP',
        description: 'POST /api/settings/known-ips',
        action: 'Add a known IP',
    },
    {
        name: 'Delete Known IP',
        value: 'deleteKnownIP',
        description: 'DELETE /api/settings/known-ips/{id}',
        action: 'Remove a known IP',
    },
    {
        name: 'Known IP Heartbeat',
        value: 'heartbeatKnownIP',
        description: 'POST /api/settings/known-ips/heartbeat',
        action: 'Register caller IP (auto-expires)',
    },
    {
        name: 'Get AI Settings (All)',
        value: 'getAiSettings',
        description: 'GET /api/settings/ai',
        action: 'All AI feature configs',
    },
    {
        name: 'Get AI Feature',
        value: 'getAiFeature',
        description: 'GET /api/settings/ai/{feature_id}',
        action: 'One AI feature',
    },
    {
        name: 'Update AI Feature',
        value: 'updateAiFeature',
        description: 'POST /api/settings/ai/{feature_id}',
        action: 'Save AI feature settings',
    },
    {
        name: 'Verify AI Feature',
        value: 'verifyAiFeature',
        description: 'POST /api/settings/ai/{feature_id}/verify',
        action: 'Test AI credentials',
    },
    {
        name: 'List AI Providers',
        value: 'listAiProviders',
        description: 'GET /api/settings/ai/providers',
        action: 'Supported providers',
    },
    {
        name: 'List AI Models',
        value: 'listAiModels',
        description: 'GET /api/settings/ai/providers/{provider}/models',
        action: 'Models for a provider',
    },
    {
        name: 'Get Email Verification Settings',
        value: 'getEmailVerification',
        description: 'GET /api/settings/email-verification',
        action: 'Read verification config',
    },
    {
        name: 'Update Email Verification',
        value: 'updateEmailVerification',
        description: 'POST /api/settings/email-verification',
        action: 'Save verification settings',
    },
    {
        name: 'Test Email Verification',
        value: 'testEmailVerification',
        description: 'POST /api/settings/email-verification/test',
        action: 'Test saved credentials',
    },
    {
        name: 'Test Custom Email Verification',
        value: 'testEmailVerificationCustom',
        description: 'POST /api/settings/email-verification/test-custom',
        action: 'Test custom provider without saving',
    },
];
exports.settingsFields = [
    {
        displayName: 'Scheduling Strategy',
        name: 'schedulingStrategy',
        type: 'options',
        options: constants_1.SCHEDULING_STRATEGY_OPTIONS,
        default: 'priority',
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['setSchedulingStrategy'],
            },
        },
    },
    {
        displayName: 'Time Offset Days',
        name: 'timeOffsetDays',
        type: 'number',
        default: 0,
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['setTimeOffset'],
            },
        },
    },
    {
        displayName: 'Test Mode',
        name: 'testModeEnabled',
        type: 'boolean',
        default: false,
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['setTestMode'],
            },
        },
    },
    {
        displayName: 'Domain',
        name: 'trackingDomainQuery',
        type: 'string',
        default: '',
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['verifyTrackingDomain'],
            },
        },
        description: 'Query param ?domain=',
    },
    {
        displayName: 'IP Address',
        name: 'knownIpAddress',
        type: 'string',
        default: '',
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['addKnownIP'],
            },
        },
    },
    {
        displayName: 'Permanent',
        name: 'knownIpPermanent',
        type: 'boolean',
        default: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['addKnownIP'],
            },
        },
    },
    (0, rlField_1.resourceLocatorField)('Known IP Entry', 'knownIpId', 'searchKnownIps', 'e.g. 3', { show: { resource: ['settings'], operation: ['deleteKnownIP'] } }, true),
    (0, rlField_1.resourceLocatorField)('AI Feature', 'aiFeatureId', 'searchAiFeatures', 'reply_classifier', {
        show: {
            resource: ['settings'],
            operation: ['getAiFeature', 'updateAiFeature', 'verifyAiFeature'],
        },
    }, true, 'Built-in features; By ID for future feature ids', 'reply_classifier'),
    {
        displayName: 'AI Update Fields',
        name: 'aiUpdateFields',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['updateAiFeature'],
            },
        },
        options: [
            {
                displayName: 'Enabled',
                name: 'enabled',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Provider',
                name: 'provider',
                type: 'string',
                default: '',
                description: 'e.g. openai, anthropic',
            },
            {
                displayName: 'Model',
                name: 'model',
                type: 'string',
                default: '',
            },
            {
                displayName: 'API Key',
                name: 'api_key',
                type: 'string',
                typeOptions: { password: true },
                default: '',
                description: 'Omit to keep existing key',
            },
        ],
    },
    {
        displayName: 'AI Verify Body (JSON)',
        name: 'aiVerifyJson',
        type: 'string',
        typeOptions: { rows: 4 },
        default: '{}',
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['verifyAiFeature'],
            },
        },
        description: 'Optional JSON body; empty fields use stored values',
    },
    {
        displayName: 'Provider',
        name: 'aiModelsProvider',
        type: 'string',
        default: 'openai',
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['listAiModels'],
            },
        },
        description: 'Path segment for /providers/{provider}/models',
    },
    {
        displayName: 'API Key (query)',
        name: 'aiModelsApiKey',
        type: 'string',
        typeOptions: { password: true },
        default: '',
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['listAiModels'],
            },
        },
        description: 'Optional ?api_key= for unsaved keys',
    },
    {
        displayName: 'Email Verification Update Fields',
        name: 'emailVerificationUpdateFields',
        type: 'collection',
        placeholder: 'Add Field',
        default: {},
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['updateEmailVerification'],
            },
        },
        options: [
            {
                displayName: 'Enabled',
                name: 'enabled',
                type: 'boolean',
                default: true,
            },
            {
                displayName: 'Provider',
                name: 'provider',
                type: 'string',
                default: 'mailtester_ninja',
                description: 'mailtester_ninja or custom',
            },
            {
                displayName: 'API Key',
                name: 'api_key',
                type: 'string',
                typeOptions: { password: true },
                default: '',
                description: 'Omit to keep existing',
            },
            {
                displayName: 'Custom URL',
                name: 'custom_url',
                type: 'string',
                default: '',
            },
            {
                displayName: 'Custom Field Path',
                name: 'custom_field_path',
                type: 'string',
                default: '',
            },
            {
                displayName: 'Custom Valid Values (comma-separated)',
                name: 'custom_valid_values_csv',
                type: 'string',
                default: '',
            },
            {
                displayName: 'Custom Invalid Values (comma-separated)',
                name: 'custom_invalid_values_csv',
                type: 'string',
                default: '',
            },
            {
                displayName: 'Custom Method',
                name: 'custom_method',
                type: 'options',
                options: [
                    { name: 'GET', value: 'GET' },
                    { name: 'POST', value: 'POST' },
                ],
                default: 'GET',
            },
        ],
    },
    {
        displayName: 'Custom Test Body (JSON)',
        name: 'emailVerificationCustomTestJson',
        type: 'string',
        typeOptions: { rows: 8 },
        default: '{\n  "url_template": "https://api.example.com/verify?email={email}",\n  "field_path": "result.status",\n  "valid_values": ["valid"],\n  "invalid_values": ["invalid"],\n  "method": "GET",\n  "test_emails": []\n}',
        required: true,
        displayOptions: {
            show: {
                resource: ['settings'],
                operation: ['testEmailVerificationCustom'],
            },
        },
    },
];
