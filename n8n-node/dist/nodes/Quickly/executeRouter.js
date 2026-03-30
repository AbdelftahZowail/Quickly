"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.executeQuickly = executeQuickly;
const form_data_1 = __importDefault(require("form-data"));
const rlUtils_1 = require("./rlUtils");
const transport_1 = require("./transport");
function numIds(arr) {
    if (!Array.isArray(arr))
        return [];
    return arr.map((x) => parseInt(String(x), 10)).filter((n) => !Number.isNaN(n));
}
function parseJson(raw, fallback) {
    const s = String(raw || '').trim();
    if (!s)
        return fallback;
    try {
        return JSON.parse(s);
    }
    catch {
        return fallback;
    }
}
/** Top-level JSON arrays from list endpoints → one n8n item per row (correct count + looping). */
function isFanOutRowArray(result) {
    if (!Array.isArray(result) || result.length === 0)
        return false;
    return result.every((x) => x !== null && typeof x === 'object' && !Array.isArray(x));
}
function leadListQs(ctx, i) {
    const q = {};
    const search = ctx.getNodeParameter('leadSearchQuery', i, '');
    if (search)
        q.q = search;
    const st = ctx.getNodeParameter('leadFilterStatus', i, '__all__');
    if (st && st !== '__all__')
        q.status = st;
    const intr = ctx.getNodeParameter('leadFilterInterest', i, '__all__');
    if (intr && intr !== '__all__')
        q.interest = intr;
    if (ctx.getNodeParameter('leadBadOnly', i, false))
        q.bad_only = true;
    return q;
}
async function binaryFromCsv(ctx, i, credentials, path, qs, fileHint) {
    const body = (await transport_1.quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path,
        qs,
        encoding: 'arraybuffer',
        json: false,
    }));
    const buf = Buffer.isBuffer(body) ? body : Buffer.from(body);
    const binary = await ctx.helpers.prepareBinaryData(buf, fileHint, 'text/csv');
    return {
        json: { fileName: fileHint, mimeType: 'text/csv' },
        binary: { data: binary },
        pairedItem: { item: i },
    };
}
function parseUniboxThreadValue(raw) {
    const parts = String(raw || '').split('||');
    if (parts.length >= 2) {
        return { inboxId: parts[0], threadId: parts.slice(1).join('||') };
    }
    return { inboxId: '', threadId: raw };
}
async function runAccount(ctx, i, credentials, operation) {
    if (operation === 'getMe') {
        return (await transport_1.quicklyRequest.call(ctx, credentials, {
            method: 'GET',
            path: '/api/auth/me',
        }));
    }
    throw new Error(`Unknown account operation: ${operation}`);
}
async function runCampaign(ctx, i, credentials, operation) {
    const cid = (0, rlUtils_1.getRl)(ctx, i, 'campaignId');
    switch (operation) {
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/campaigns',
            }));
        case 'get':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${cid}`,
            }));
        case 'create': {
            const name = ctx.getNodeParameter('name', i, '');
            const inboxIds = numIds(ctx.getNodeParameter('inboxIds', i, []));
            const extra = ctx.getNodeParameter('additionalFields', i, {});
            const body = { name, inbox_ids: inboxIds };
            const days = extra.sending_days;
            if (days === null || days === void 0 ? void 0 : days.length)
                body.sending_days = days.map((d) => parseInt(String(d), 10));
            if (extra.sending_hours_start)
                body.sending_hours_start = extra.sending_hours_start;
            if (extra.sending_hours_end)
                body.sending_hours_end = extra.sending_hours_end;
            if (extra.stop_on_reply !== undefined)
                body.stop_on_reply = extra.stop_on_reply;
            if (extra.paused !== undefined)
                body.paused = extra.paused;
            if (extra.priority !== undefined && extra.priority !== 0 && extra.priority !== '') {
                body.priority = extra.priority;
            }
            if (extra.timezone)
                body.timezone = extra.timezone;
            if (extra.track_opens !== undefined)
                body.track_opens = extra.track_opens;
            if (extra.track_clicks !== undefined)
                body.track_clicks = extra.track_clicks;
            if (extra.add_unsubscribe_header !== undefined) {
                body.add_unsubscribe_header = extra.add_unsubscribe_header;
            }
            if (extra.send_first_as_text !== undefined)
                body.send_first_as_text = extra.send_first_as_text;
            if (extra.send_all_as_text !== undefined)
                body.send_all_as_text = extra.send_all_as_text;
            if (extra.match_lead_provider !== undefined)
                body.match_lead_provider = extra.match_lead_provider;
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/campaigns',
                body,
            }));
        }
        case 'update': {
            const patch = ctx.getNodeParameter('updateFields', i, {});
            const body = { ...patch };
            if (patch.inbox_ids)
                body.inbox_ids = numIds(patch.inbox_ids);
            if (patch.sending_days) {
                body.sending_days = patch.sending_days.map((d) => parseInt(String(d), 10));
            }
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'PATCH',
                path: `/api/campaigns/${cid}`,
                body,
            }));
        }
        case 'delete':
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/campaigns/${cid}`,
            });
            return { success: true };
        case 'duplicate':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${cid}/duplicate`,
            }));
        case 'reorder': {
            const raw = ctx.getNodeParameter('campaignIdsOrdered', i, '[]');
            const ids = parseJson(raw, []);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/campaigns/reorder',
                body: { campaign_ids: ids },
            }));
        }
        case 'hasLeads':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/campaigns/has-leads',
            }));
        case 'getQueue':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${cid}/queue`,
            }));
        case 'getSent':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${cid}/sent`,
            }));
        case 'recalculateQueue':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${cid}/recalculate-queue`,
            }));
        case 'getAnalyticsSteps':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${cid}/analytics/steps`,
            }));
        case 'previewEmail':
        case 'sendTestEmail': {
            const sequenceId = parseInt((0, rlUtils_1.getRl)(ctx, i, 'sequenceId'), 10);
            const leadRaw = (0, rlUtils_1.getRl)(ctx, i, 'previewLeadId');
            const variantRaw = (0, rlUtils_1.getRl)(ctx, i, 'variantId');
            const body = { sequence_id: sequenceId };
            if (leadRaw)
                body.lead_id = parseInt(leadRaw, 10);
            if (variantRaw && variantRaw !== '__none__')
                body.variant_id = parseInt(variantRaw, 10);
            if (operation === 'previewEmail') {
                return (await transport_1.quicklyRequest.call(ctx, credentials, {
                    method: 'POST',
                    path: `/api/campaigns/${cid}/preview`,
                    body,
                }));
            }
            body.to_email = ctx.getNodeParameter('toEmail', i, '');
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${cid}/send-test`,
                body,
            }));
        }
        default:
            throw new Error(`Unknown campaign operation: ${operation}`);
    }
}
async function runSequence(ctx, i, credentials, operation) {
    const campaignId = (0, rlUtils_1.getRl)(ctx, i, 'campaignId');
    const seqId = (0, rlUtils_1.getRl)(ctx, i, 'sequenceId');
    switch (operation) {
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${campaignId}/sequences`,
            }));
        case 'create': {
            const extra = ctx.getNodeParameter('sequenceCreateFields', i, {});
            const body = {
                position: ctx.getNodeParameter('position', i, 1),
                body: ctx.getNodeParameter('body', i, ''),
                wait_days_after_previous: ctx.getNodeParameter('wait_days_after_previous', i, 0),
                ...extra,
            };
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${campaignId}/sequences`,
                body,
            }));
        }
        case 'update': {
            const patch = ctx.getNodeParameter('sequenceUpdateFields', i, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'PATCH',
                path: `/api/campaigns/${campaignId}/sequences/${seqId}`,
                body: patch,
            }));
        }
        case 'delete':
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/campaigns/${campaignId}/sequences/${seqId}`,
            });
            return { success: true };
        default:
            throw new Error(`Unknown sequence operation: ${operation}`);
    }
}
async function runSequenceVariant(ctx, i, credentials, operation) {
    const campaignId = (0, rlUtils_1.getRl)(ctx, i, 'campaignId');
    const sequenceId = (0, rlUtils_1.getRl)(ctx, i, 'sequenceId');
    const variantId = (0, rlUtils_1.getRl)(ctx, i, 'variantId');
    const base = `/api/campaigns/${campaignId}/sequences/${sequenceId}/variants`;
    switch (operation) {
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, { method: 'GET', path: base }));
        case 'create': {
            const extra = ctx.getNodeParameter('variantCreateFields', i, {});
            const body = {
                body: ctx.getNodeParameter('variantBody', i, ''),
                ...extra,
            };
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: base,
                body,
            }));
        }
        case 'update': {
            const patch = ctx.getNodeParameter('variantUpdateFields', i, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'PATCH',
                path: `${base}/${variantId}`,
                body: patch,
            }));
        }
        case 'delete':
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `${base}/${variantId}`,
            });
            return { success: true };
        default:
            throw new Error(`Unknown sequence variant operation: ${operation}`);
    }
}
async function runLead(ctx, i, credentials, operation) {
    const qsBase = leadListQs(ctx, i);
    switch (operation) {
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/leads',
                qs: qsBase,
            }));
        case 'exportCsv':
            return binaryFromCsv(ctx, i, credentials, '/api/leads/export', qsBase, 'leads_export.csv');
        case 'get':
        case 'update':
        case 'delete':
        case 'recover':
        case 'getHistory':
        case 'getReplies': {
            const lid = (0, rlUtils_1.getRl)(ctx, i, 'leadId');
            if (operation === 'get') {
                return (await transport_1.quicklyRequest.call(ctx, credentials, {
                    method: 'GET',
                    path: `/api/leads/${lid}`,
                }));
            }
            if (operation === 'update') {
                const f = ctx.getNodeParameter('leadUpdateFields', i, {});
                const body = {};
                if ('name' in f)
                    body.name = f.name;
                if ('custom_data_json' in f && f.custom_data_json) {
                    const cd = parseJson(f.custom_data_json, {});
                    if (Object.keys(cd).length)
                        body.custom_data = cd;
                }
                if ('enrollment_status' in f && f.enrollment_status) {
                    body.enrollment_status = f.enrollment_status;
                }
                return (await transport_1.quicklyRequest.call(ctx, credentials, {
                    method: 'PATCH',
                    path: `/api/leads/${lid}`,
                    body,
                }));
            }
            if (operation === 'delete') {
                await transport_1.quicklyRequest.call(ctx, credentials, {
                    method: 'DELETE',
                    path: `/api/leads/${lid}`,
                });
                return { success: true };
            }
            if (operation === 'recover') {
                return (await transport_1.quicklyRequest.call(ctx, credentials, {
                    method: 'POST',
                    path: `/api/leads/${lid}/recover`,
                    body: {
                        email: ctx.getNodeParameter('recoverEmail', i, ''),
                        verify_email: ctx.getNodeParameter('recoverVerifyEmail', i, true),
                    },
                }));
            }
            if (operation === 'getHistory') {
                return (await transport_1.quicklyRequest.call(ctx, credentials, {
                    method: 'GET',
                    path: `/api/leads/${lid}/history`,
                }));
            }
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/leads/${lid}/replies`,
            }));
        }
        case 'bulkDelete':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/leads/bulk-delete',
                body: { lead_ids: numIds(ctx.getNodeParameter('leadIds', i, [])) },
            }));
        case 'bulkStatus':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/leads/bulk-status',
                body: {
                    lead_ids: numIds(ctx.getNodeParameter('leadIds', i, [])),
                    enrollment_status: ctx.getNodeParameter('enrollmentStatus', i, 'active'),
                },
            }));
        case 'bulkRecover': {
            const col = ctx.getNodeParameter('bulkRecoverItems', i, {});
            const rows = col.item || [];
            const items = rows.map((r) => ({
                lead_id: parseInt((0, rlUtils_1.getResourceLocatorValue)(r.lead_id), 10),
                email: String(r.email || '').trim(),
            }));
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/leads/bulk-recover',
                body: {
                    items,
                    verify_email: ctx.getNodeParameter('verifyEmailAfterRecover', i, true),
                },
            }));
        }
        case 'markReplied':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/leads/mark-replied',
                body: {
                    lead_id: parseInt((0, rlUtils_1.getRl)(ctx, i, 'markRepliedLeadId'), 10),
                    campaign_id: parseInt((0, rlUtils_1.getRl)(ctx, i, 'markRepliedCampaignId'), 10),
                },
            }));
        default:
            throw new Error(`Unknown lead operation: ${operation}`);
    }
}
async function runCampaignLead(ctx, i, credentials, operation) {
    const campaignId = (0, rlUtils_1.getRl)(ctx, i, 'campaignId');
    const skipDup = ctx.getNodeParameter('skipDuplicates', i, true);
    const verifyOnAdd = ctx.getNodeParameter('verifyEmailsOnAdd', i, false);
    const qsAdd = {};
    if (operation === 'add' || operation === 'importCsv') {
        qsAdd.skip_duplicates = skipDup;
        qsAdd.verify_emails = verifyOnAdd;
    }
    switch (operation) {
        case 'add': {
            const leads = parseJson(ctx.getNodeParameter('leadsJson', i, '[]'), []);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${campaignId}/leads`,
                qs: qsAdd,
                body: leads,
            }));
        }
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${campaignId}/leads`,
            }));
        case 'remove': {
            const leadId = (0, rlUtils_1.getRl)(ctx, i, 'campaignLeadId');
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/campaigns/${campaignId}/leads/${leadId}`,
            });
            return { success: true };
        }
        case 'updateEnrollment': {
            const f = ctx.getNodeParameter('enrollmentUpdateFields', i, {});
            const body = {};
            if ('status' in f &&
                f.status !== undefined &&
                f.status !== null &&
                String(f.status).trim() !== '') {
                body.status = f.status;
            }
            if ('interest' in f) {
                if (f.interest === undefined || f.interest === null) {
                    /* collection key present but unset — skip */
                }
                else if (String(f.interest).trim() === '') {
                    body.interest = '';
                }
                else {
                    body.interest = f.interest;
                }
            }
            if ('sending_paused' in f && f.sending_paused !== undefined && f.sending_paused !== null) {
                body.sending_paused = f.sending_paused;
            }
            const leadId = (0, rlUtils_1.getRl)(ctx, i, 'campaignLeadId');
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'PATCH',
                path: `/api/campaigns/${campaignId}/leads/${leadId}`,
                body,
            }));
        }
        case 'detectProviders':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${campaignId}/leads/detect-providers`,
            }));
        case 'verifyEmails':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${campaignId}/leads/verify`,
            }));
        case 'verificationStatus':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/campaigns/${campaignId}/leads/verification-status`,
            }));
        case 'exportCsv': {
            const q = {};
            const vs = ctx.getNodeParameter('exportVerificationStatus', i, '');
            if (vs)
                q.verification_status = vs;
            const es = ctx.getNodeParameter('exportEnrollmentStatus', i, '__all__');
            if (es && es !== '__all__')
                q.status = es;
            const intr = ctx.getNodeParameter('exportInterest', i, '__all__');
            if (intr && intr !== '__all__')
                q.interest = intr;
            return binaryFromCsv(ctx, i, credentials, `/api/campaigns/${campaignId}/leads/export`, q, 'campaign_leads_export.csv');
        }
        case 'importCsv': {
            const binProp = ctx.getNodeParameter('importBinaryProperty', i, 'data');
            const fileName = ctx.getNodeParameter('importFileName', i, 'leads.csv') || 'leads.csv';
            const buffer = await ctx.helpers.getBinaryDataBuffer(i, binProp);
            const form = new form_data_1.default();
            form.append('file', buffer, { filename: fileName });
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/campaigns/${campaignId}/leads/import`,
                qs: qsAdd,
                body: form,
                json: false,
            }));
        }
        default:
            throw new Error(`Unknown campaign lead operation: ${operation}`);
    }
}
async function runInbox(ctx, i, credentials, operation) {
    const iid = (0, rlUtils_1.getRl)(ctx, i, 'inboxId');
    switch (operation) {
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/inboxes',
            }));
        case 'get':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/inboxes/${iid}`,
            }));
        case 'create': {
            const extra = ctx.getNodeParameter('inboxCreateFields', i, {});
            const body = {
                email: ctx.getNodeParameter('inboxEmail', i, ''),
                ...extra,
            };
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/inboxes',
                body,
            }));
        }
        case 'update': {
            const patch = ctx.getNodeParameter('inboxUpdateFields', i, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'PATCH',
                path: `/api/inboxes/${iid}`,
                body: patch,
            }));
        }
        case 'delete':
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/inboxes/${iid}`,
            });
            return { success: true };
        case 'pause':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/inboxes/${iid}/pause`,
                body: { action: ctx.getNodeParameter('inboxPauseAction', i, 'pause_leads') },
            }));
        case 'unpause':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/inboxes/${iid}/unpause`,
            }));
        default:
            throw new Error(`Unknown inbox operation: ${operation}`);
    }
}
async function runSchedule(ctx, i, credentials, operation) {
    switch (operation) {
        case 'getSent':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/schedule/sent',
            }));
        case 'getScheduled':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/schedule/scheduled',
            }));
        case 'getStats':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/schedule/stats',
            }));
        case 'validateQueue':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/schedule/validate-queue',
            }));
        case 'recalculateAll': {
            const sync = ctx.getNodeParameter('recalculateSync', i, false);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/schedule/recalculate-all',
                qs: sync ? { sync: true } : {},
            }));
        }
        case 'recordOpen':
        case 'recordClick': {
            const logId = (0, rlUtils_1.getRl)(ctx, i, 'emailLogId');
            const ip = ctx.getNodeParameter('eventIp', i, '');
            const body = {};
            if (ip)
                body.ip = ip;
            const path = operation === 'recordOpen'
                ? `/api/schedule/sent/${logId}/open`
                : `/api/schedule/sent/${logId}/click`;
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path,
                body,
            }));
        }
        default:
            throw new Error(`Unknown schedule operation: ${operation}`);
    }
}
async function runStatus(ctx, i, credentials, operation) {
    if (operation === 'getStatus') {
        return (await transport_1.quicklyRequest.call(ctx, credentials, {
            method: 'GET',
            path: '/api/status',
        }));
    }
    if (operation === 'getSystemHealth') {
        return (await transport_1.quicklyRequest.call(ctx, credentials, {
            method: 'GET',
            path: '/api/system-health',
        }));
    }
    throw new Error(`Unknown status operation: ${operation}`);
}
async function runSettings(ctx, i, credentials, operation) {
    switch (operation) {
        case 'getSchedulingStrategy':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/scheduling-strategy',
            }));
        case 'setSchedulingStrategy':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/scheduling-strategy',
                body: { scheduling_strategy: ctx.getNodeParameter('schedulingStrategy', i, 'priority') },
            }));
        case 'getTimeOffset':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/time-offset',
            }));
        case 'setTimeOffset':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/time-offset',
                body: { time_offset_days: ctx.getNodeParameter('timeOffsetDays', i, 0) },
            }));
        case 'getTestMode':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/test-mode',
            }));
        case 'setTestMode':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/test-mode',
                body: { test_mode: ctx.getNodeParameter('testModeEnabled', i, false) },
            }));
        case 'getServerInfo':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/server-info',
            }));
        case 'verifyTrackingDomain':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/verify-tracking-domain',
                qs: { domain: ctx.getNodeParameter('trackingDomainQuery', i, '') },
            }));
        case 'getMcpSetup':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/mcp-setup',
            }));
        case 'listKnownIPs':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/known-ips',
            }));
        case 'addKnownIP':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/known-ips',
                body: {
                    ip_address: ctx.getNodeParameter('knownIpAddress', i, ''),
                    permanent: ctx.getNodeParameter('knownIpPermanent', i, true),
                },
            }));
        case 'deleteKnownIP': {
            const id = (0, rlUtils_1.getRl)(ctx, i, 'knownIpId');
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/settings/known-ips/${id}`,
            });
            return { success: true };
        }
        case 'heartbeatKnownIP':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/known-ips/heartbeat',
            }));
        case 'getAiSettings':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/ai',
            }));
        case 'getAiFeature': {
            const fid = (0, rlUtils_1.getRl)(ctx, i, 'aiFeatureId', 'reply_classifier');
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/settings/ai/${fid}`,
            }));
        }
        case 'updateAiFeature': {
            const fid = (0, rlUtils_1.getRl)(ctx, i, 'aiFeatureId', 'reply_classifier');
            const f = ctx.getNodeParameter('aiUpdateFields', i, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/settings/ai/${fid}`,
                body: f,
            }));
        }
        case 'verifyAiFeature': {
            const fid = (0, rlUtils_1.getRl)(ctx, i, 'aiFeatureId', 'reply_classifier');
            const raw = ctx.getNodeParameter('aiVerifyJson', i, '{}');
            const body = parseJson(raw, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/settings/ai/${fid}/verify`,
                body,
            }));
        }
        case 'listAiProviders':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/ai/providers',
            }));
        case 'listAiModels': {
            const provider = ctx.getNodeParameter('aiModelsProvider', i, 'openai');
            const key = ctx.getNodeParameter('aiModelsApiKey', i, '');
            const qs = {};
            if (key)
                qs.api_key = key;
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/settings/ai/providers/${encodeURIComponent(provider)}/models`,
                qs,
            }));
        }
        case 'getEmailVerification':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/email-verification',
            }));
        case 'updateEmailVerification': {
            const f = ctx.getNodeParameter('emailVerificationUpdateFields', i, {});
            const body = { ...f };
            const vCsv = f.custom_valid_values_csv;
            const iCsv = f.custom_invalid_values_csv;
            delete body.custom_valid_values_csv;
            delete body.custom_invalid_values_csv;
            if (vCsv) {
                body.custom_valid_values = vCsv
                    .split(',')
                    .map((s) => s.trim())
                    .filter(Boolean);
            }
            if (iCsv) {
                body.custom_invalid_values = iCsv
                    .split(',')
                    .map((s) => s.trim())
                    .filter(Boolean);
            }
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/email-verification',
                body,
            }));
        }
        case 'testEmailVerification':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/email-verification/test',
            }));
        case 'testEmailVerificationCustom': {
            const raw = ctx.getNodeParameter('emailVerificationCustomTestJson', i, '{}');
            const body = parseJson(raw, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/email-verification/test-custom',
                body,
            }));
        }
        default:
            throw new Error(`Unknown settings operation: ${operation}`);
    }
}
async function runWebhook(ctx, i, credentials, operation) {
    switch (operation) {
        case 'listEventTypes':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/webhooks/events',
            }));
        case 'getAll':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/settings/webhooks',
            }));
        case 'create': {
            const extra = ctx.getNodeParameter('webhookCreateFields', i, {});
            const body = {
                url: ctx.getNodeParameter('webhookUrl', i, ''),
                events: ctx.getNodeParameter('webhookEvents', i, []),
                ...extra,
            };
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/settings/webhooks',
                body,
            }));
        }
        case 'update': {
            const wid = (0, rlUtils_1.getRl)(ctx, i, 'webhookId');
            const patch = ctx.getNodeParameter('webhookUpdateFields', i, {});
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'PATCH',
                path: `/api/settings/webhooks/${wid}`,
                body: patch,
            }));
        }
        case 'delete': {
            const wid = (0, rlUtils_1.getRl)(ctx, i, 'webhookId');
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/settings/webhooks/${wid}`,
            });
            return { success: true };
        }
        case 'test': {
            const wid = (0, rlUtils_1.getRl)(ctx, i, 'webhookId');
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/settings/webhooks/${wid}/test`,
            }));
        }
        default:
            throw new Error(`Unknown webhook operation: ${operation}`);
    }
}
async function runNotification(ctx, i, credentials, operation) {
    if (operation === 'getConfig') {
        return (await transport_1.quicklyRequest.call(ctx, credentials, {
            method: 'GET',
            path: '/api/notifications/config',
        }));
    }
    if (operation === 'updateConfig') {
        return (await transport_1.quicklyRequest.call(ctx, credentials, {
            method: 'PUT',
            path: '/api/notifications/config',
            body: {
                enabled: ctx.getNodeParameter('notificationEnabled', i, true),
                notification_email: ctx.getNodeParameter('notificationEmail', i, ''),
                events: ctx.getNodeParameter('notificationEvents', i, []),
                rate_limit_per_hour: ctx.getNodeParameter('notificationRateLimit', i, 10),
            },
        }));
    }
    throw new Error(`Unknown notification operation: ${operation}`);
}
async function runUnibox(ctx, i, credentials, operation) {
    switch (operation) {
        case 'listConversations':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/unibox',
                qs: {
                    page: ctx.getNodeParameter('uniboxPage', i, 1),
                    page_size: ctx.getNodeParameter('uniboxPageSize', i, 20),
                    leads_only: ctx.getNodeParameter('uniboxLeadsOnly', i, false),
                },
            }));
        case 'getSyncStatus': {
            const inbox = (0, rlUtils_1.getRl)(ctx, i, 'uniboxFilterInboxId');
            const qs = {};
            if (inbox)
                qs.inbox_id = parseInt(inbox, 10);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/unibox/status',
                qs,
            }));
        }
        case 'getUnreadCount':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/unibox/notifications',
            }));
        case 'getThread': {
            const raw = (0, rlUtils_1.getRl)(ctx, i, 'uniboxThreadId');
            const { inboxId, threadId } = parseUniboxThreadValue(raw);
            const override = (0, rlUtils_1.getRl)(ctx, i, 'uniboxThreadInboxId');
            const useInbox = override || inboxId;
            const qs = {};
            if (useInbox)
                qs.inbox_id = parseInt(useInbox, 10);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: `/api/unibox/threads/${encodeURIComponent(threadId)}`,
                qs,
            }));
        }
        case 'markThreadRead': {
            const raw = (0, rlUtils_1.getRl)(ctx, i, 'uniboxThreadId');
            const { inboxId, threadId } = parseUniboxThreadValue(raw);
            if (!inboxId)
                throw new Error('Thread option must include inbox id (reload thread list)');
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: `/api/unibox/threads/${encodeURIComponent(threadId)}/mark-read`,
                qs: { inbox_id: parseInt(inboxId, 10) },
            }));
        }
        case 'sync': {
            const inbox = (0, rlUtils_1.getRl)(ctx, i, 'uniboxSyncInboxId');
            const body = {};
            if (inbox)
                body.inbox_id = parseInt(inbox, 10);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/unibox/sync',
                body,
            }));
        }
        case 'loadMore': {
            const inbox = (0, rlUtils_1.getRl)(ctx, i, 'uniboxSyncInboxId');
            const body = {
                window_days: ctx.getNodeParameter('uniboxWindowDays', i, 30),
            };
            if (inbox)
                body.inbox_id = parseInt(inbox, 10);
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/unibox/load-more',
                body,
            }));
        }
        case 'sendEmail': {
            const sendExtra = ctx.getNodeParameter('uniboxSendFields', i, {});
            const body = {
                inbox_id: parseInt((0, rlUtils_1.getRl)(ctx, i, 'uniboxSendInboxId'), 10),
                to_email: ctx.getNodeParameter('uniboxToEmail', i, ''),
                subject: ctx.getNodeParameter('uniboxSubject', i, ''),
                body: ctx.getNodeParameter('uniboxBody', i, ''),
                ...sendExtra,
            };
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'POST',
                path: '/api/unibox/send',
                body,
            }));
        }
        default:
            throw new Error(`Unknown unibox operation: ${operation}`);
    }
}
async function runEmailAccount(ctx, i, credentials, operation) {
    switch (operation) {
        case 'gmailStatus':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/gmail/status',
            }));
        case 'gmailListAccounts':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/gmail/accounts',
            }));
        case 'gmailDisconnect': {
            const id = (0, rlUtils_1.getRl)(ctx, i, 'gmailAccountId');
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/gmail/accounts/${id}`,
            });
            return { success: true };
        }
        case 'gmailPermissions':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/gmail/permissions',
            }));
        case 'office365Status':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/office365/status',
            }));
        case 'office365ListAccounts':
            return (await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'GET',
                path: '/api/office365/accounts',
            }));
        case 'office365Disconnect': {
            const id = (0, rlUtils_1.getRl)(ctx, i, 'office365AccountId');
            await transport_1.quicklyRequest.call(ctx, credentials, {
                method: 'DELETE',
                path: `/api/office365/accounts/${id}`,
            });
            return { success: true };
        }
        default:
            throw new Error(`Unknown email account operation: ${operation}`);
    }
}
async function dispatch(ctx, i, credentials, resource, operation) {
    switch (resource) {
        case 'account':
            return runAccount(ctx, i, credentials, operation);
        case 'campaign':
            return runCampaign(ctx, i, credentials, operation);
        case 'sequence':
            return runSequence(ctx, i, credentials, operation);
        case 'sequenceVariant':
            return runSequenceVariant(ctx, i, credentials, operation);
        case 'lead':
            return runLead(ctx, i, credentials, operation);
        case 'campaignLead':
            return runCampaignLead(ctx, i, credentials, operation);
        case 'inbox':
            return runInbox(ctx, i, credentials, operation);
        case 'schedule':
            return runSchedule(ctx, i, credentials, operation);
        case 'status':
            return runStatus(ctx, i, credentials, operation);
        case 'settings':
            return runSettings(ctx, i, credentials, operation);
        case 'webhook':
            return runWebhook(ctx, i, credentials, operation);
        case 'notification':
            return runNotification(ctx, i, credentials, operation);
        case 'unibox':
            return runUnibox(ctx, i, credentials, operation);
        case 'emailAccount':
            return runEmailAccount(ctx, i, credentials, operation);
        default:
            throw new Error(`Unknown resource: ${resource}`);
    }
}
async function executeQuickly() {
    const items = this.getInputData();
    const returnData = [];
    const credentials = await this.getCredentials('quicklyApi');
    for (let i = 0; i < items.length; i++) {
        try {
            const resource = this.getNodeParameter('resource', i);
            const operation = this.getNodeParameter('operation', i);
            if (!operation) {
                throw new Error('Select an operation');
            }
            const result = await dispatch(this, i, credentials, resource, operation);
            if (result &&
                typeof result === 'object' &&
                'pairedItem' in result &&
                'binary' in result) {
                returnData.push(result);
                continue;
            }
            if (Array.isArray(result) && result.length === 0) {
                continue;
            }
            if (isFanOutRowArray(result)) {
                for (const row of result) {
                    returnData.push({
                        json: row,
                        pairedItem: { item: i },
                    });
                }
                continue;
            }
            const data = result;
            returnData.push({
                json: data !== null && typeof data === 'object' ? data : { result: data },
                pairedItem: { item: i },
            });
        }
        catch (error) {
            if (this.continueOnFail()) {
                returnData.push({
                    json: { error: error instanceof Error ? error.message : String(error) },
                    pairedItem: { item: i },
                });
                continue;
            }
            throw error;
        }
    }
    return [returnData];
}
