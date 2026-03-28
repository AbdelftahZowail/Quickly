"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.loadOperations = loadOperations;
exports.getCampaigns = getCampaigns;
exports.getInboxes = getInboxes;
exports.getLeads = getLeads;
exports.getSequencesForCampaign = getSequencesForCampaign;
exports.getVariantsForSequence = getVariantsForSequence;
exports.getLeadsForPreview = getLeadsForPreview;
exports.getLeadsForCampaign = getLeadsForCampaign;
exports.getWebhooks = getWebhooks;
exports.getWebhookEventTypes = getWebhookEventTypes;
exports.getKnownIps = getKnownIps;
exports.getAiFeatureIds = getAiFeatureIds;
exports.getGmailAccounts = getGmailAccounts;
exports.getOffice365Accounts = getOffice365Accounts;
exports.getUniboxThreads = getUniboxThreads;
exports.getScheduleSentLogs = getScheduleSentLogs;
const operationsCatalog_1 = require("./operationsCatalog");
const transport_1 = require("./transport");
async function loadOperations() {
    const resource = this.getCurrentNodeParameter('resource');
    const list = operationsCatalog_1.OPERATIONS_BY_RESOURCE[resource];
    if (!(list === null || list === void 0 ? void 0 : list.length)) {
        return [{ name: '— Select a resource first —', value: '' }];
    }
    return list;
}
async function getCampaigns() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/campaigns',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.slice(0, 500).map((c) => {
            var _a;
            return ({
                name: `${String((_a = c.name) !== null && _a !== void 0 ? _a : '')} (#${c.id})`,
                value: String(c.id),
            });
        });
    }
    catch {
        return [{ name: '— Could not load campaigns —', value: '' }];
    }
}
async function getInboxes() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/inboxes',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.slice(0, 500).map((r) => ({
            name: `${String(r.display_name || r.email || 'Inbox')} (#${r.id})`,
            value: String(r.id),
        }));
    }
    catch {
        return [{ name: '— Could not load inboxes —', value: '' }];
    }
}
async function getLeads() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/leads',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.slice(0, 500).map((r) => {
            var _a;
            return ({
                name: `${String((_a = r.email) !== null && _a !== void 0 ? _a : r.id)} (#${r.id})`,
                value: String(r.id),
                description: String(r.name || ''),
            });
        });
    }
    catch {
        return [{ name: '— Could not load leads —', value: '' }];
    }
}
async function getSequencesForCampaign() {
    const campaignId = this.getCurrentNodeParameter('campaignId');
    if (!campaignId) {
        return [{ name: '— Select a campaign first —', value: '' }];
    }
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: `/api/campaigns/${campaignId}/sequences`,
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.map((s, idx) => {
            var _a;
            return ({
                name: `Step ${(_a = s.position) !== null && _a !== void 0 ? _a : idx + 1} — ${String(s.subject || '(no subject)')} (#${s.id})`,
                value: String(s.id),
            });
        });
    }
    catch {
        return [{ name: '— Could not load sequences —', value: '' }];
    }
}
async function getVariantsForSequence() {
    const campaignId = this.getCurrentNodeParameter('campaignId');
    const sequenceId = this.getCurrentNodeParameter('sequenceId');
    if (!campaignId || !sequenceId) {
        return [{ name: '— Select campaign and sequence first —', value: '' }];
    }
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: `/api/campaigns/${campaignId}/sequences/${sequenceId}/variants`,
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        if (rows.length === 0) {
            return [{ name: '— No variants (optional) —', value: '__none__' }];
        }
        return [
            { name: '— Default sequence content —', value: '__none__' },
            ...rows.map((v) => ({
                name: `${String(v.label || 'Variant')} (#${v.id})`,
                value: String(v.id),
            })),
        ];
    }
    catch {
        return [{ name: '— Could not load variants —', value: '' }];
    }
}
async function getLeadsForPreview() {
    const campaignId = this.getCurrentNodeParameter('campaignId');
    if (!campaignId) {
        return [{ name: '— Select a campaign first —', value: '' }];
    }
    return getLeadsForCampaign.call(this);
}
async function getLeadsForCampaign() {
    const campaignId = this.getCurrentNodeParameter('campaignId');
    if (!campaignId) {
        return [{ name: '— Select a campaign first —', value: '' }];
    }
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: `/api/campaigns/${campaignId}/leads`,
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.slice(0, 500).map((r) => ({
            name: `${String(r.email)} (#${r.lead_id})`,
            value: String(r.lead_id),
        }));
    }
    catch {
        return [{ name: '— Could not load campaign leads —', value: '' }];
    }
}
async function getWebhooks() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/settings/webhooks',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.map((w) => ({
            name: `${String(w.description || w.url)} (#${w.id})`,
            value: String(w.id),
        }));
    }
    catch {
        return [{ name: '— Could not load webhooks —', value: '' }];
    }
}
async function getWebhookEventTypes() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/settings/webhooks/events',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.map((e) => ({ name: e, value: e }));
    }
    catch {
        return [{ name: '— Could not load events —', value: '' }];
    }
}
async function getKnownIps() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const data = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/settings/known-ips',
        }));
        const rows = data.known_ips;
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.map((r) => ({
            name: `${String(r.ip_address)} (#${r.id})`,
            value: String(r.id),
        }));
    }
    catch {
        return [{ name: '— Could not load known IPs —', value: '' }];
    }
}
async function getAiFeatureIds() {
    return [{ name: 'Reply Interest Classifier', value: 'reply_classifier' }];
}
async function getGmailAccounts() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/gmail/accounts',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.map((a) => ({
            name: `${String(a.google_email || a.inbox_email)} (#${a.id})`,
            value: String(a.id),
        }));
    }
    catch {
        return [{ name: '— Could not load Gmail accounts —', value: '' }];
    }
}
async function getOffice365Accounts() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/office365/accounts',
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.map((a) => ({
            name: `${String(a.microsoft_email)} (#${a.id})`,
            value: String(a.id),
        }));
    }
    catch {
        return [{ name: '— Could not load Office 365 accounts —', value: '' }];
    }
}
async function getUniboxThreads() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const data = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/unibox',
            qs: { page: 1, page_size: 100, leads_only: false },
        }));
        const items = data.items;
        if (!Array.isArray(items)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return items.map((it) => {
            var _a, _b, _c;
            const tid = String((_a = it.thread_id) !== null && _a !== void 0 ? _a : '');
            const iid = String((_b = it.inbox_id) !== null && _b !== void 0 ? _b : '');
            const sub = String((_c = it.subject) !== null && _c !== void 0 ? _c : '(no subject)');
            return {
                name: `${sub.slice(0, 60)}${sub.length > 60 ? '…' : ''} [inbox ${iid}]`,
                value: `${iid}||${tid}`,
                description: String(it.lead_email || it.inbox_account || ''),
            };
        });
    }
    catch {
        return [{ name: '— Could not load threads —', value: '' }];
    }
}
async function getScheduleSentLogs() {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/schedule/sent',
            qs: { limit: 200, offset: 0, include_events: false, include_body: false },
        }));
        if (!Array.isArray(rows)) {
            return [{ name: '— Invalid response —', value: '' }];
        }
        return rows.slice(0, 200).map((r) => ({
            name: `#${r.log_id} — ${String(r.lead_email)} — ${String(r.subject || '').slice(0, 40)}`,
            value: String(r.log_id),
        }));
    }
    catch {
        return [{ name: '— Could not load sent logs —', value: '' }];
    }
}
