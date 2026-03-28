"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.statusFields = exports.statusOperations = void 0;
exports.statusOperations = [
    {
        name: 'Get App Status',
        value: 'getStatus',
        description: 'GET /api/status',
        action: 'Scheduler and health banner data',
    },
    {
        name: 'Get System Health',
        value: 'getSystemHealth',
        description: 'GET /api/system-health',
        action: 'OAuth, inboxes, unibox, AI, verification snapshot',
    },
];
exports.statusFields = [];
