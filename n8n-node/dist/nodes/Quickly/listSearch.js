"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.searchCampaigns = searchCampaigns;
exports.searchInboxes = searchInboxes;
exports.searchLeads = searchLeads;
exports.searchSequencesForCampaign = searchSequencesForCampaign;
exports.searchVariantsForSequence = searchVariantsForSequence;
exports.searchLeadsForCampaign = searchLeadsForCampaign;
exports.searchWebhooks = searchWebhooks;
exports.searchKnownIps = searchKnownIps;
exports.searchAiFeatures = searchAiFeatures;
exports.searchGmailAccounts = searchGmailAccounts;
exports.searchOffice365Accounts = searchOffice365Accounts;
exports.searchUniboxThreads = searchUniboxThreads;
exports.searchScheduleSentLogs = searchScheduleSentLogs;
const rlUtils_1 = require("./rlUtils");
const transport_1 = require("./transport");
function currentRl(ctx, param) {
    try {
        return (0, rlUtils_1.getResourceLocatorValue)(ctx.getCurrentNodeParameter(param));
    }
    catch {
        return '';
    }
}
function applyFilter(items, filter) {
    const f = (filter || '').trim().toLowerCase();
    if (!f)
        return items.slice(0, 100);
    return items
        .filter((it) => String(it.name || '')
        .toLowerCase()
        .includes(f) ||
        String(it.value || '')
            .toLowerCase()
            .includes(f) ||
        String(it.description || '')
            .toLowerCase()
            .includes(f))
        .slice(0, 100);
}
async function searchCampaigns(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/campaigns',
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.slice(0, 500).map((c) => {
            var _a, _b, _c, _d, _e;
            return ({
                name: `${String((_a = c.name) !== null && _a !== void 0 ? _a : '')} (#${c.id})`,
                value: String(c.id),
                description: `Leads: ${(_c = (_b = c.stats) === null || _b === void 0 ? void 0 : _b.total_leads) !== null && _c !== void 0 ? _c : '—'} · Sent: ${(_e = (_d = c.stats) === null || _d === void 0 ? void 0 : _d.emails_sent) !== null && _e !== void 0 ? _e : '—'}`,
            });
        });
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchInboxes(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/inboxes',
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.slice(0, 500).map((r) => ({
            name: `${String(r.display_name || r.email || 'Inbox')} (#${r.id})`,
            value: String(r.id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchLeads(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/leads',
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.slice(0, 500).map((r) => {
            var _a;
            return ({
                name: `${String((_a = r.email) !== null && _a !== void 0 ? _a : r.id)} (#${r.id})`,
                value: String(r.id),
                description: String(r.name || ''),
            });
        });
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchSequencesForCampaign(filter) {
    const campaignId = currentRl(this, 'campaignId');
    if (!campaignId)
        return { results: [] };
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: `/api/campaigns/${campaignId}/sequences`,
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.map((s, idx) => {
            var _a;
            return ({
                name: `Step ${(_a = s.position) !== null && _a !== void 0 ? _a : idx + 1} — ${String(s.subject || '(no subject)')} (#${s.id})`,
                value: String(s.id),
            });
        });
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchVariantsForSequence(filter) {
    const campaignId = currentRl(this, 'campaignId');
    const sequenceId = currentRl(this, 'sequenceId');
    if (!campaignId || !sequenceId)
        return { results: [] };
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: `/api/campaigns/${campaignId}/sequences/${sequenceId}/variants`,
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = [
            { name: '— Default sequence content —', value: '__none__' },
            ...rows.map((v) => ({
                name: `${String(v.label || 'Variant')} (#${v.id})`,
                value: String(v.id),
            })),
        ];
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchLeadsForCampaign(filter) {
    const campaignId = currentRl(this, 'campaignId');
    if (!campaignId)
        return { results: [] };
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: `/api/campaigns/${campaignId}/leads`,
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.slice(0, 500).map((r) => ({
            name: `${String(r.email)} (#${r.lead_id})`,
            value: String(r.lead_id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchWebhooks(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/settings/webhooks',
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.map((w) => ({
            name: `${String(w.description || w.url)} (#${w.id})`,
            value: String(w.id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchKnownIps(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const data = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/settings/known-ips',
        }));
        const rows = data.known_ips;
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.map((r) => ({
            name: `${String(r.ip_address)} (#${r.id})`,
            value: String(r.id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchAiFeatures(filter) {
    const results = [
        {
            name: 'Reply Interest Classifier',
            value: 'reply_classifier',
        },
    ];
    return { results: applyFilter(results, filter) };
}
async function searchGmailAccounts(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/gmail/accounts',
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.map((a) => ({
            name: `${String(a.google_email || a.inbox_email)} (#${a.id})`,
            value: String(a.id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchOffice365Accounts(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/office365/accounts',
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.map((a) => ({
            name: `${String(a.microsoft_email)} (#${a.id})`,
            value: String(a.id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchUniboxThreads(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const data = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/unibox',
            qs: { page: 1, page_size: 100, leads_only: false },
        }));
        const items = data.items;
        if (!Array.isArray(items))
            return { results: [] };
        const results = items.map((it) => {
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
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
async function searchScheduleSentLogs(filter) {
    const credentials = await this.getCredentials('quicklyApi');
    try {
        const rows = (await transport_1.quicklyRequest.call(this, credentials, {
            method: 'GET',
            path: '/api/schedule/sent',
            qs: { limit: 200, offset: 0, include_events: false, include_body: false },
        }));
        if (!Array.isArray(rows))
            return { results: [] };
        const results = rows.slice(0, 200).map((r) => ({
            name: `#${r.log_id} — ${String(r.lead_email)} — ${String(r.subject || '').slice(0, 40)}`,
            value: String(r.log_id),
        }));
        return { results: applyFilter(results, filter) };
    }
    catch {
        return { results: [] };
    }
}
