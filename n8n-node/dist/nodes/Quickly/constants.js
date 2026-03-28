"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.PAUSE_INBOX_ACTION_OPTIONS = exports.SCHEDULING_STRATEGY_OPTIONS = exports.INBOX_PROVIDER_OPTIONS = exports.INTEREST_ENROLLMENT_OPTIONS = exports.INTEREST_FILTER_OPTIONS = exports.LEAD_LIST_STATUS_OPTIONS = exports.ENROLLMENT_STATUS_OPTIONS = exports.WEEKDAY_OPTIONS = void 0;
exports.WEEKDAY_OPTIONS = [
    { name: 'Monday', value: '0' },
    { name: 'Tuesday', value: '1' },
    { name: 'Wednesday', value: '2' },
    { name: 'Thursday', value: '3' },
    { name: 'Friday', value: '4' },
    { name: 'Saturday', value: '5' },
    { name: 'Sunday', value: '6' },
];
exports.ENROLLMENT_STATUS_OPTIONS = [
    { name: 'Active', value: 'active' },
    { name: 'Contacted', value: 'contacted' },
    { name: 'Completed', value: 'completed' },
    { name: 'Bounced', value: 'bounced' },
    { name: 'Unsubscribed', value: 'unsubscribed' },
    { name: 'Wrong Person', value: 'wrong_person' },
];
exports.LEAD_LIST_STATUS_OPTIONS = [
    ...exports.ENROLLMENT_STATUS_OPTIONS,
    { name: 'Invalid (verification / legacy)', value: 'invalid' },
    { name: 'Replied', value: 'replied' },
];
exports.INTEREST_FILTER_OPTIONS = [
    { name: 'Interested', value: 'interested' },
    { name: 'Not Interested', value: 'not_interested' },
    { name: 'Out of Office', value: 'out_of_office' },
    { name: 'Auto Reply', value: 'auto_reply' },
    { name: 'Unset / None', value: 'unset' },
];
exports.INTEREST_ENROLLMENT_OPTIONS = [
    { name: 'Interested', value: 'interested' },
    { name: 'Not Interested', value: 'not_interested' },
    { name: 'Out of Office', value: 'out_of_office' },
    { name: 'Auto Reply', value: 'auto_reply' },
    { name: 'Clear', value: '' },
];
exports.INBOX_PROVIDER_OPTIONS = [
    { name: 'Gmail', value: 'gmail' },
    { name: 'Office 365', value: 'office365' },
];
exports.SCHEDULING_STRATEGY_OPTIONS = [
    { name: 'Priority', value: 'priority' },
    { name: 'Round Robin', value: 'round_robin' },
];
exports.PAUSE_INBOX_ACTION_OPTIONS = [
    { name: 'Pause Leads', value: 'pause_leads' },
    { name: 'Reassign', value: 'reassign' },
];
