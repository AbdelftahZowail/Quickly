"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.OPERATIONS_BY_RESOURCE = exports.RESOURCE_OPTIONS = void 0;
const account_operations_1 = require("./account.operations");
const campaigns_operations_1 = require("./campaigns.operations");
const campaignLeads_operations_1 = require("./campaignLeads.operations");
const emailAccounts_operations_1 = require("./emailAccounts.operations");
const inboxes_operations_1 = require("./inboxes.operations");
const leads_operations_1 = require("./leads.operations");
const notifications_operations_1 = require("./notifications.operations");
const schedule_operations_1 = require("./schedule.operations");
const sequenceVariants_operations_1 = require("./sequenceVariants.operations");
const sequences_operations_1 = require("./sequences.operations");
const settings_operations_1 = require("./settings.operations");
const status_operations_1 = require("./status.operations");
const unibox_operations_1 = require("./unibox.operations");
const webhooks_operations_1 = require("./webhooks.operations");
exports.RESOURCE_OPTIONS = [
    { name: 'Account', value: 'account' },
    { name: 'Campaign', value: 'campaign' },
    { name: 'Sequence', value: 'sequence' },
    { name: 'Sequence Variant', value: 'sequenceVariant' },
    { name: 'Lead', value: 'lead' },
    { name: 'Campaign Lead', value: 'campaignLead' },
    { name: 'Inbox', value: 'inbox' },
    { name: 'Schedule', value: 'schedule' },
    { name: 'Status', value: 'status' },
    { name: 'Settings', value: 'settings' },
    { name: 'Webhook', value: 'webhook' },
    { name: 'Notification', value: 'notification' },
    { name: 'Unibox', value: 'unibox' },
    { name: 'Email Account (OAuth)', value: 'emailAccount' },
];
exports.OPERATIONS_BY_RESOURCE = {
    account: account_operations_1.accountOperations,
    campaign: campaigns_operations_1.campaignOperations,
    sequence: sequences_operations_1.sequenceOperations,
    sequenceVariant: sequenceVariants_operations_1.sequenceVariantOperations,
    lead: leads_operations_1.leadOperations,
    campaignLead: campaignLeads_operations_1.campaignLeadOperations,
    inbox: inboxes_operations_1.inboxOperations,
    schedule: schedule_operations_1.scheduleOperations,
    status: status_operations_1.statusOperations,
    settings: settings_operations_1.settingsOperations,
    webhook: webhooks_operations_1.webhookOperations,
    notification: notifications_operations_1.notificationOperations,
    unibox: unibox_operations_1.uniboxOperations,
    emailAccount: emailAccounts_operations_1.emailAccountOperations,
};
