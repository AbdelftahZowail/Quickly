"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.scheduleFields = exports.scheduleOperations = void 0;
const rlField_1 = require("./rlField");
exports.scheduleOperations = [
    {
        name: 'Get Sent',
        value: 'getSent',
        description: 'GET /api/schedule/sent',
        action: 'All sent emails',
    },
    {
        name: 'Get Scheduled',
        value: 'getScheduled',
        description: 'GET /api/schedule/scheduled',
        action: 'Upcoming queue slots',
    },
    {
        name: 'Get Stats',
        value: 'getStats',
        description: 'GET /api/schedule/stats',
        action: 'Summary counts',
    },
    {
        name: 'Validate Queue',
        value: 'validateQueue',
        description: 'POST /api/schedule/validate-queue',
        action: 'Run validation checks',
    },
    {
        name: 'Recalculate All',
        value: 'recalculateAll',
        description: 'POST /api/schedule/recalculate-all',
        action: 'Rebuild all campaign queues',
    },
    {
        name: 'Record Open',
        value: 'recordOpen',
        description: 'POST /api/schedule/sent/{log_id}/open',
        action: 'Manually record an open',
    },
    {
        name: 'Record Click',
        value: 'recordClick',
        description: 'POST /api/schedule/sent/{log_id}/click',
        action: 'Manually record a click',
    },
];
exports.scheduleFields = [
    {
        displayName: 'Synchronous',
        name: 'recalculateSync',
        type: 'boolean',
        default: false,
        displayOptions: {
            show: {
                resource: ['schedule'],
                operation: ['recalculateAll'],
            },
        },
        description: 'When true, adds ?sync=true and waits for completion',
    },
    (0, rlField_1.resourceLocatorField)('Sent Email Log', 'emailLogId', 'searchScheduleSentLogs', 'e.g. 1001', {
        show: {
            resource: ['schedule'],
            operation: ['recordOpen', 'recordClick'],
        },
    }, true),
    {
        displayName: 'IP Address',
        name: 'eventIp',
        type: 'string',
        default: '',
        displayOptions: {
            show: {
                resource: ['schedule'],
                operation: ['recordOpen', 'recordClick'],
            },
        },
        description: 'Optional IP stored with the event',
    },
];
