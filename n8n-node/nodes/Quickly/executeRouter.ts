import FormData from 'form-data';
import type {
  IDataObject,
  IExecuteFunctions,
  INodeExecutionData,
} from 'n8n-workflow';
import { getResourceLocatorValue, getRl } from './rlUtils';
import { quicklyRequest } from './transport';

function numIds(arr: unknown): number[] {
  if (!Array.isArray(arr)) return [];
  return arr.map((x) => parseInt(String(x), 10)).filter((n) => !Number.isNaN(n));
}

function parseJson<T>(raw: string, fallback: T): T {
  const s = String(raw || '').trim();
  if (!s) return fallback;
  try {
    return JSON.parse(s) as T;
  } catch {
    return fallback;
  }
}

/** Top-level JSON arrays from list endpoints → one n8n item per row (correct count + looping). */
function isFanOutRowArray(result: unknown): result is IDataObject[] {
  if (!Array.isArray(result) || result.length === 0) return false;
  return result.every(
    (x) => x !== null && typeof x === 'object' && !Array.isArray(x),
  );
}

function leadListQs(
  ctx: IExecuteFunctions,
  i: number,
): IDataObject {
  const q: IDataObject = {};
  const search = ctx.getNodeParameter('leadSearchQuery', i, '') as string;
  if (search) q.q = search;
  const st = ctx.getNodeParameter('leadFilterStatus', i, '__all__') as string;
  if (st && st !== '__all__') q.status = st;
  const intr = ctx.getNodeParameter('leadFilterInterest', i, '__all__') as string;
  if (intr && intr !== '__all__') q.interest = intr;
  if (ctx.getNodeParameter('leadBadOnly', i, false)) q.bad_only = true;
  return q;
}

async function binaryFromCsv(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  path: string,
  qs: IDataObject,
  fileHint: string,
): Promise<INodeExecutionData> {
  const body = (await quicklyRequest.call(ctx, credentials, {
    method: 'GET',
    path,
    qs,
    encoding: 'arraybuffer',
    json: false,
  })) as ArrayBuffer | Buffer;
  const buf = Buffer.isBuffer(body) ? body : Buffer.from(body);
  const binary = await ctx.helpers.prepareBinaryData(buf, fileHint, 'text/csv');
  return {
    json: { fileName: fileHint, mimeType: 'text/csv' },
    binary: { data: binary },
    pairedItem: { item: i },
  };
}

function parseUniboxThreadValue(raw: string): { inboxId: string; threadId: string } {
  const parts = String(raw || '').split('||');
  if (parts.length >= 2) {
    return { inboxId: parts[0], threadId: parts.slice(1).join('||') };
  }
  return { inboxId: '', threadId: raw };
}

async function runAccount(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject | INodeExecutionData> {
  if (operation === 'getMe') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'GET',
      path: '/api/auth/me',
    })) as IDataObject;
  }
  throw new Error(`Unknown account operation: ${operation}`);
}

async function runCampaign(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject | INodeExecutionData> {
  const cid = getRl(ctx, i, 'campaignId');
  switch (operation) {
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/campaigns',
      })) as IDataObject;
    case 'get':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${cid}`,
      })) as IDataObject;
    case 'create': {
      const name = ctx.getNodeParameter('name', i, '') as string;
      const inboxIds = numIds(ctx.getNodeParameter('inboxIds', i, []));
      const extra = ctx.getNodeParameter('additionalFields', i, {}) as IDataObject;
      const body: IDataObject = { name, inbox_ids: inboxIds };
      const days = extra.sending_days as string[] | undefined;
      if (days?.length) body.sending_days = days.map((d) => parseInt(String(d), 10));
      if (extra.sending_hours_start) body.sending_hours_start = extra.sending_hours_start;
      if (extra.sending_hours_end) body.sending_hours_end = extra.sending_hours_end;
      if (extra.stop_on_reply !== undefined) body.stop_on_reply = extra.stop_on_reply;
      if (extra.paused !== undefined) body.paused = extra.paused;
      if (extra.priority !== undefined && extra.priority !== 0 && extra.priority !== '') {
        body.priority = extra.priority;
      }
      if (extra.timezone) body.timezone = extra.timezone;
      if (extra.track_opens !== undefined) body.track_opens = extra.track_opens;
      if (extra.track_clicks !== undefined) body.track_clicks = extra.track_clicks;
      if (extra.add_unsubscribe_header !== undefined) {
        body.add_unsubscribe_header = extra.add_unsubscribe_header;
      }
      if (extra.send_first_as_text !== undefined) body.send_first_as_text = extra.send_first_as_text;
      if (extra.send_all_as_text !== undefined) body.send_all_as_text = extra.send_all_as_text;
      if (extra.match_lead_provider !== undefined) body.match_lead_provider = extra.match_lead_provider;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/campaigns',
        body,
      })) as IDataObject;
    }
    case 'update': {
      const patch = ctx.getNodeParameter('updateFields', i, {}) as IDataObject;
      const body: IDataObject = { ...patch };
      if (patch.inbox_ids) body.inbox_ids = numIds(patch.inbox_ids);
      if (patch.sending_days) {
        body.sending_days = (patch.sending_days as string[]).map((d) => parseInt(String(d), 10));
      }
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'PATCH',
        path: `/api/campaigns/${cid}`,
        body,
      })) as IDataObject;
    }
    case 'delete':
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/campaigns/${cid}`,
      });
      return { success: true };
    case 'duplicate':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${cid}/duplicate`,
      })) as IDataObject;
    case 'reorder': {
      const raw = ctx.getNodeParameter('campaignIdsOrdered', i, '[]') as string;
      const ids = parseJson<number[]>(raw, []);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/campaigns/reorder',
        body: { campaign_ids: ids },
      })) as IDataObject;
    }
    case 'hasLeads':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/campaigns/has-leads',
      })) as IDataObject;
    case 'getQueue':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${cid}/queue`,
      })) as IDataObject;
    case 'getSent':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${cid}/sent`,
      })) as IDataObject;
    case 'recalculateQueue':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${cid}/recalculate-queue`,
      })) as IDataObject;
    case 'getAnalyticsSteps':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${cid}/analytics/steps`,
      })) as IDataObject;
    case 'previewEmail':
    case 'sendTestEmail': {
      const sequenceId = parseInt(getRl(ctx, i, 'sequenceId'), 10);
      const leadRaw = getRl(ctx, i, 'previewLeadId');
      const variantRaw = getRl(ctx, i, 'variantId');
      const body: IDataObject = { sequence_id: sequenceId };
      if (leadRaw) body.lead_id = parseInt(leadRaw, 10);
      if (variantRaw && variantRaw !== '__none__') body.variant_id = parseInt(variantRaw, 10);
      if (operation === 'previewEmail') {
        return (await quicklyRequest.call(ctx, credentials, {
          method: 'POST',
          path: `/api/campaigns/${cid}/preview`,
          body,
        })) as IDataObject;
      }
      body.to_email = ctx.getNodeParameter('toEmail', i, '') as string;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${cid}/send-test`,
        body,
      })) as IDataObject;
    }
    default:
      throw new Error(`Unknown campaign operation: ${operation}`);
  }
}

async function runSequence(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  const campaignId = getRl(ctx, i, 'campaignId');
  const seqId = getRl(ctx, i, 'sequenceId');
  switch (operation) {
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${campaignId}/sequences`,
      })) as IDataObject;
    case 'create': {
      const extra = ctx.getNodeParameter('sequenceCreateFields', i, {}) as IDataObject;
      const body: IDataObject = {
        position: ctx.getNodeParameter('position', i, 1),
        body: ctx.getNodeParameter('body', i, ''),
        wait_days_after_previous: ctx.getNodeParameter('wait_days_after_previous', i, 0),
        ...extra,
      };
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${campaignId}/sequences`,
        body,
      })) as IDataObject;
    }
    case 'update': {
      const patch = ctx.getNodeParameter('sequenceUpdateFields', i, {}) as IDataObject;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'PATCH',
        path: `/api/campaigns/${campaignId}/sequences/${seqId}`,
        body: patch,
      })) as IDataObject;
    }
    case 'delete':
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/campaigns/${campaignId}/sequences/${seqId}`,
      });
      return { success: true };
    default:
      throw new Error(`Unknown sequence operation: ${operation}`);
  }
}

async function runSequenceVariant(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  const campaignId = getRl(ctx, i, 'campaignId');
  const sequenceId = getRl(ctx, i, 'sequenceId');
  const variantId = getRl(ctx, i, 'variantId');
  const base = `/api/campaigns/${campaignId}/sequences/${sequenceId}/variants`;
  switch (operation) {
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, { method: 'GET', path: base })) as IDataObject;
    case 'create': {
      const extra = ctx.getNodeParameter('variantCreateFields', i, {}) as IDataObject;
      const body: IDataObject = {
        body: ctx.getNodeParameter('variantBody', i, ''),
        ...extra,
      };
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: base,
        body,
      })) as IDataObject;
    }
    case 'update': {
      const patch = ctx.getNodeParameter('variantUpdateFields', i, {}) as IDataObject;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'PATCH',
        path: `${base}/${variantId}`,
        body: patch,
      })) as IDataObject;
    }
    case 'delete':
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `${base}/${variantId}`,
      });
      return { success: true };
    default:
      throw new Error(`Unknown sequence variant operation: ${operation}`);
  }
}

async function runLead(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject | INodeExecutionData> {
  const qsBase = leadListQs(ctx, i);
  switch (operation) {
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/leads',
        qs: qsBase,
      })) as IDataObject;
    case 'exportCsv':
      return binaryFromCsv(ctx, i, credentials, '/api/leads/export', qsBase, 'leads_export.csv');
    case 'get':
    case 'update':
    case 'delete':
    case 'recover':
    case 'getHistory':
    case 'getReplies': {
      const lid = getRl(ctx, i, 'leadId');
      if (operation === 'get') {
        return (await quicklyRequest.call(ctx, credentials, {
          method: 'GET',
          path: `/api/leads/${lid}`,
        })) as IDataObject;
      }
      if (operation === 'update') {
        const f = ctx.getNodeParameter('leadUpdateFields', i, {}) as IDataObject;
        const body: IDataObject = {};
        if ('name' in f) body.name = f.name;
        if ('custom_data_json' in f && f.custom_data_json) {
          const cd = parseJson<IDataObject>(f.custom_data_json as string, {});
          if (Object.keys(cd).length) body.custom_data = cd;
        }
        if ('enrollment_status' in f && f.enrollment_status) {
          body.enrollment_status = f.enrollment_status;
        }
        return (await quicklyRequest.call(ctx, credentials, {
          method: 'PATCH',
          path: `/api/leads/${lid}`,
          body,
        })) as IDataObject;
      }
      if (operation === 'delete') {
        await quicklyRequest.call(ctx, credentials, {
          method: 'DELETE',
          path: `/api/leads/${lid}`,
        });
        return { success: true };
      }
      if (operation === 'recover') {
        return (await quicklyRequest.call(ctx, credentials, {
          method: 'POST',
          path: `/api/leads/${lid}/recover`,
          body: {
            email: ctx.getNodeParameter('recoverEmail', i, ''),
            verify_email: ctx.getNodeParameter('recoverVerifyEmail', i, true),
          },
        })) as IDataObject;
      }
      if (operation === 'getHistory') {
        return (await quicklyRequest.call(ctx, credentials, {
          method: 'GET',
          path: `/api/leads/${lid}/history`,
        })) as IDataObject;
      }
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/leads/${lid}/replies`,
      })) as IDataObject;
    }
    case 'bulkDelete':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/leads/bulk-delete',
        body: { lead_ids: numIds(ctx.getNodeParameter('leadIds', i, [])) },
      })) as IDataObject;
    case 'bulkStatus':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/leads/bulk-status',
        body: {
          lead_ids: numIds(ctx.getNodeParameter('leadIds', i, [])),
          enrollment_status: ctx.getNodeParameter('enrollmentStatus', i, 'active'),
        },
      })) as IDataObject;
    case 'bulkRecover': {
      const col = ctx.getNodeParameter('bulkRecoverItems', i, {}) as { item?: IDataObject[] };
      const rows = col.item || [];
      const items = rows.map((r) => ({
        lead_id: parseInt(getResourceLocatorValue(r.lead_id), 10),
        email: String(r.email || '').trim(),
      }));
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/leads/bulk-recover',
        body: {
          items,
          verify_email: ctx.getNodeParameter('verifyEmailAfterRecover', i, true),
        },
      })) as IDataObject;
    }
    case 'markReplied':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/leads/mark-replied',
        body: {
          lead_id: parseInt(getRl(ctx, i, 'markRepliedLeadId'), 10),
          campaign_id: parseInt(getRl(ctx, i, 'markRepliedCampaignId'), 10),
        },
      })) as IDataObject;
    default:
      throw new Error(`Unknown lead operation: ${operation}`);
  }
}

async function runCampaignLead(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject | INodeExecutionData> {
  const campaignId = getRl(ctx, i, 'campaignId');
  const skipDup = ctx.getNodeParameter('skipDuplicates', i, true);
  const verifyOnAdd = ctx.getNodeParameter('verifyEmailsOnAdd', i, false);
  const qsAdd: IDataObject = {};
  if (operation === 'add' || operation === 'importCsv') {
    qsAdd.skip_duplicates = skipDup;
    qsAdd.verify_emails = verifyOnAdd;
  }
  switch (operation) {
    case 'add': {
      const leads = parseJson<IDataObject[]>(ctx.getNodeParameter('leadsJson', i, '[]') as string, []);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${campaignId}/leads`,
        qs: qsAdd,
        body: leads,
      })) as IDataObject;
    }
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${campaignId}/leads`,
      })) as IDataObject;
    case 'remove': {
      const leadId = getRl(ctx, i, 'campaignLeadId');
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/campaigns/${campaignId}/leads/${leadId}`,
      });
      return { success: true };
    }
    case 'updateEnrollment': {
      const f = ctx.getNodeParameter('enrollmentUpdateFields', i, {}) as IDataObject;
      const body: IDataObject = {};
      if (
        'status' in f &&
        f.status !== undefined &&
        f.status !== null &&
        String(f.status).trim() !== ''
      ) {
        body.status = f.status;
      }
      if ('interest' in f) {
        if (f.interest === undefined || f.interest === null) {
          /* collection key present but unset — skip */
        } else if (String(f.interest).trim() === '') {
          body.interest = '';
        } else {
          body.interest = f.interest;
        }
      }
      if ('sending_paused' in f && f.sending_paused !== undefined && f.sending_paused !== null) {
        body.sending_paused = f.sending_paused;
      }
      const leadId = getRl(ctx, i, 'campaignLeadId');
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'PATCH',
        path: `/api/campaigns/${campaignId}/leads/${leadId}`,
        body,
      })) as IDataObject;
    }
    case 'detectProviders':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${campaignId}/leads/detect-providers`,
      })) as IDataObject;
    case 'verifyEmails':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${campaignId}/leads/verify`,
      })) as IDataObject;
    case 'verificationStatus':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/campaigns/${campaignId}/leads/verification-status`,
      })) as IDataObject;
    case 'exportCsv': {
      const q: IDataObject = {};
      const vs = ctx.getNodeParameter('exportVerificationStatus', i, '') as string;
      if (vs) q.verification_status = vs;
      const es = ctx.getNodeParameter('exportEnrollmentStatus', i, '__all__') as string;
      if (es && es !== '__all__') q.status = es;
      const intr = ctx.getNodeParameter('exportInterest', i, '__all__') as string;
      if (intr && intr !== '__all__') q.interest = intr;
      return binaryFromCsv(
        ctx,
        i,
        credentials,
        `/api/campaigns/${campaignId}/leads/export`,
        q,
        'campaign_leads_export.csv',
      );
    }
    case 'importCsv': {
      const binProp = ctx.getNodeParameter('importBinaryProperty', i, 'data') as string;
      const fileName = (ctx.getNodeParameter('importFileName', i, 'leads.csv') as string) || 'leads.csv';
      const buffer = await ctx.helpers.getBinaryDataBuffer(i, binProp);
      const form = new FormData();
      form.append('file', buffer, { filename: fileName });
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/campaigns/${campaignId}/leads/import`,
        qs: qsAdd,
        body: form,
        json: false,
      })) as IDataObject;
    }
    default:
      throw new Error(`Unknown campaign lead operation: ${operation}`);
  }
}

async function runInbox(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  const iid = getRl(ctx, i, 'inboxId');
  switch (operation) {
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/inboxes',
      })) as IDataObject;
    case 'get':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/inboxes/${iid}`,
      })) as IDataObject;
    case 'create': {
      const extra = ctx.getNodeParameter('inboxCreateFields', i, {}) as IDataObject;
      const body: IDataObject = {
        email: ctx.getNodeParameter('inboxEmail', i, ''),
        ...extra,
      };
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/inboxes',
        body,
      })) as IDataObject;
    }
    case 'update': {
      const patch = ctx.getNodeParameter('inboxUpdateFields', i, {}) as IDataObject;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'PATCH',
        path: `/api/inboxes/${iid}`,
        body: patch,
      })) as IDataObject;
    }
    case 'delete':
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/inboxes/${iid}`,
      });
      return { success: true };
    case 'pause':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/inboxes/${iid}/pause`,
        body: { action: ctx.getNodeParameter('inboxPauseAction', i, 'pause_leads') },
      })) as IDataObject;
    case 'unpause':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/inboxes/${iid}/unpause`,
      })) as IDataObject;
    default:
      throw new Error(`Unknown inbox operation: ${operation}`);
  }
}

async function runSchedule(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  switch (operation) {
    case 'getSent':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/schedule/sent',
      })) as IDataObject;
    case 'getScheduled':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/schedule/scheduled',
      })) as IDataObject;
    case 'getStats':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/schedule/stats',
      })) as IDataObject;
    case 'validateQueue':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/schedule/validate-queue',
      })) as IDataObject;
    case 'recalculateAll': {
      const sync = ctx.getNodeParameter('recalculateSync', i, false);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/schedule/recalculate-all',
        qs: sync ? { sync: true } : {},
      })) as IDataObject;
    }
    case 'recordOpen':
    case 'recordClick': {
      const logId = getRl(ctx, i, 'emailLogId');
      const ip = ctx.getNodeParameter('eventIp', i, '') as string;
      const body: IDataObject = {};
      if (ip) body.ip = ip;
      const path =
        operation === 'recordOpen'
          ? `/api/schedule/sent/${logId}/open`
          : `/api/schedule/sent/${logId}/click`;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path,
        body,
      })) as IDataObject;
    }
    default:
      throw new Error(`Unknown schedule operation: ${operation}`);
  }
}

async function runStatus(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  if (operation === 'getStatus') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'GET',
      path: '/api/status',
    })) as IDataObject;
  }
  if (operation === 'getSystemHealth') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'GET',
      path: '/api/system-health',
    })) as IDataObject;
  }
  throw new Error(`Unknown status operation: ${operation}`);
}

async function runSettings(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  switch (operation) {
    case 'getSchedulingStrategy':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/scheduling-strategy',
      })) as IDataObject;
    case 'setSchedulingStrategy':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/scheduling-strategy',
        body: { scheduling_strategy: ctx.getNodeParameter('schedulingStrategy', i, 'priority') },
      })) as IDataObject;
    case 'getTimeOffset':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/time-offset',
      })) as IDataObject;
    case 'setTimeOffset':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/time-offset',
        body: { time_offset_days: ctx.getNodeParameter('timeOffsetDays', i, 0) },
      })) as IDataObject;
    case 'getTestMode':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/test-mode',
      })) as IDataObject;
    case 'setTestMode':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/test-mode',
        body: { test_mode: ctx.getNodeParameter('testModeEnabled', i, false) },
      })) as IDataObject;
    case 'getServerInfo':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/server-info',
      })) as IDataObject;
    case 'verifyTrackingDomain':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/verify-tracking-domain',
        qs: { domain: ctx.getNodeParameter('trackingDomainQuery', i, '') },
      })) as IDataObject;
    case 'getMcpSetup':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/mcp-setup',
      })) as IDataObject;
    case 'listKnownIPs':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/known-ips',
      })) as IDataObject;
    case 'addKnownIP':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/known-ips',
        body: {
          ip_address: ctx.getNodeParameter('knownIpAddress', i, ''),
          permanent: ctx.getNodeParameter('knownIpPermanent', i, true),
        },
      })) as IDataObject;
    case 'deleteKnownIP': {
      const id = getRl(ctx, i, 'knownIpId');
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/settings/known-ips/${id}`,
      });
      return { success: true };
    }
    case 'heartbeatKnownIP':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/known-ips/heartbeat',
      })) as IDataObject;
    case 'getAiSettings':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/ai',
      })) as IDataObject;
    case 'getAiFeature': {
      const fid = getRl(ctx, i, 'aiFeatureId', 'reply_classifier');
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/settings/ai/${fid}`,
      })) as IDataObject;
    }
    case 'updateAiFeature': {
      const fid = getRl(ctx, i, 'aiFeatureId', 'reply_classifier');
      const f = ctx.getNodeParameter('aiUpdateFields', i, {}) as IDataObject;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/settings/ai/${fid}`,
        body: f,
      })) as IDataObject;
    }
    case 'verifyAiFeature': {
      const fid = getRl(ctx, i, 'aiFeatureId', 'reply_classifier');
      const raw = ctx.getNodeParameter('aiVerifyJson', i, '{}') as string;
      const body = parseJson<IDataObject>(raw, {});
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/settings/ai/${fid}/verify`,
        body,
      })) as IDataObject;
    }
    case 'listAiProviders':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/ai/providers',
      })) as IDataObject;
    case 'listAiModels': {
      const provider = ctx.getNodeParameter('aiModelsProvider', i, 'openai') as string;
      const key = ctx.getNodeParameter('aiModelsApiKey', i, '') as string;
      const qs: IDataObject = {};
      if (key) qs.api_key = key;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/settings/ai/providers/${encodeURIComponent(provider)}/models`,
        qs,
      })) as IDataObject;
    }
    case 'getEmailVerification':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/email-verification',
      })) as IDataObject;
    case 'updateEmailVerification': {
      const f = ctx.getNodeParameter('emailVerificationUpdateFields', i, {}) as IDataObject;
      const body: IDataObject = { ...f };
      const vCsv = f.custom_valid_values_csv as string | undefined;
      const iCsv = f.custom_invalid_values_csv as string | undefined;
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
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/email-verification',
        body,
      })) as IDataObject;
    }
    case 'testEmailVerification':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/email-verification/test',
      })) as IDataObject;
    case 'testEmailVerificationCustom': {
      const raw = ctx.getNodeParameter('emailVerificationCustomTestJson', i, '{}') as string;
      const body = parseJson<IDataObject>(raw, {});
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/email-verification/test-custom',
        body,
      })) as IDataObject;
    }
    default:
      throw new Error(`Unknown settings operation: ${operation}`);
  }
}

async function runWebhook(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  switch (operation) {
    case 'listEventTypes':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/webhooks/events',
      })) as IDataObject;
    case 'getAll':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/settings/webhooks',
      })) as IDataObject;
    case 'create': {
      const extra = ctx.getNodeParameter('webhookCreateFields', i, {}) as IDataObject;
      const body: IDataObject = {
        url: ctx.getNodeParameter('webhookUrl', i, ''),
        events: ctx.getNodeParameter('webhookEvents', i, []),
        ...extra,
      };
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/settings/webhooks',
        body,
      })) as IDataObject;
    }
    case 'update': {
      const wid = getRl(ctx, i, 'webhookId');
      const patch = ctx.getNodeParameter('webhookUpdateFields', i, {}) as IDataObject;
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'PATCH',
        path: `/api/settings/webhooks/${wid}`,
        body: patch,
      })) as IDataObject;
    }
    case 'delete': {
      const wid = getRl(ctx, i, 'webhookId');
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/settings/webhooks/${wid}`,
      });
      return { success: true };
    }
    case 'test': {
      const wid = getRl(ctx, i, 'webhookId');
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/settings/webhooks/${wid}/test`,
      })) as IDataObject;
    }
    default:
      throw new Error(`Unknown webhook operation: ${operation}`);
  }
}

async function runNotification(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  if (operation === 'list') {
    const qs: IDataObject = {};
    const unreadOnly = ctx.getNodeParameter('unreadOnly', i, false) as boolean;
    if (unreadOnly) qs.unread_only = 'true';
    qs.limit = String(ctx.getNodeParameter('limit', i, 50));
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'GET',
      path: '/api/notifications',
      qs,
    })) as IDataObject;
  }
  if (operation === 'markRead') {
    const id = ctx.getNodeParameter('notificationId', i, 0) as number;
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'PATCH',
      path: `/api/notifications/${id}/read`,
    })) as IDataObject;
  }
  if (operation === 'markAllRead') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'POST',
      path: '/api/notifications/read-all',
    })) as IDataObject;
  }
  if (operation === 'delete') {
    const id = ctx.getNodeParameter('notificationId', i, 0) as number;
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'DELETE',
      path: `/api/notifications/${id}`,
    })) as IDataObject;
  }
  if (operation === 'unreadCount') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'GET',
      path: '/api/notifications/unread-count',
    })) as IDataObject;
  }
  if (operation === 'getConfig') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'GET',
      path: '/api/notifications/config',
    })) as IDataObject;
  }
  if (operation === 'updateConfig') {
    return (await quicklyRequest.call(ctx, credentials, {
      method: 'PUT',
      path: '/api/notifications/config',
      body: {
        enabled: ctx.getNodeParameter('notificationEnabled', i, true),
        notification_email: ctx.getNodeParameter('notificationEmail', i, ''),
        events: ctx.getNodeParameter('notificationEvents', i, []),
        rate_limit_per_hour: ctx.getNodeParameter('notificationRateLimit', i, 10),
      },
    })) as IDataObject;
  }
  throw new Error(`Unknown notification operation: ${operation}`);
}

async function runUnibox(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  switch (operation) {
    case 'listConversations':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/unibox',
        qs: {
          page: ctx.getNodeParameter('uniboxPage', i, 1),
          page_size: ctx.getNodeParameter('uniboxPageSize', i, 20),
          leads_only: ctx.getNodeParameter('uniboxLeadsOnly', i, false),
        },
      })) as IDataObject;
    case 'getSyncStatus': {
      const inbox = getRl(ctx, i, 'uniboxFilterInboxId');
      const qs: IDataObject = {};
      if (inbox) qs.inbox_id = parseInt(inbox, 10);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/unibox/status',
        qs,
      })) as IDataObject;
    }
    case 'getUnreadCount':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/unibox/notifications',
      })) as IDataObject;
    case 'getThread': {
      const raw = getRl(ctx, i, 'uniboxThreadId');
      const { inboxId, threadId } = parseUniboxThreadValue(raw);
      const override = getRl(ctx, i, 'uniboxThreadInboxId');
      const useInbox = override || inboxId;
      const qs: IDataObject = {};
      if (useInbox) qs.inbox_id = parseInt(useInbox, 10);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: `/api/unibox/threads/${encodeURIComponent(threadId)}`,
        qs,
      })) as IDataObject;
    }
    case 'markThreadRead': {
      const raw = getRl(ctx, i, 'uniboxThreadId');
      const { inboxId, threadId } = parseUniboxThreadValue(raw);
      if (!inboxId) throw new Error('Thread option must include inbox id (reload thread list)');
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: `/api/unibox/threads/${encodeURIComponent(threadId)}/mark-read`,
        qs: { inbox_id: parseInt(inboxId, 10) },
      })) as IDataObject;
    }
    case 'sync': {
      const inbox = getRl(ctx, i, 'uniboxSyncInboxId');
      const body: IDataObject = {};
      if (inbox) body.inbox_id = parseInt(inbox, 10);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/unibox/sync',
        body,
      })) as IDataObject;
    }
    case 'loadMore': {
      const inbox = getRl(ctx, i, 'uniboxSyncInboxId');
      const body: IDataObject = {
        window_days: ctx.getNodeParameter('uniboxWindowDays', i, 30),
      };
      if (inbox) body.inbox_id = parseInt(inbox, 10);
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/unibox/load-more',
        body,
      })) as IDataObject;
    }
    case 'sendEmail': {
      const sendExtra = ctx.getNodeParameter('uniboxSendFields', i, {}) as IDataObject;
      const body: IDataObject = {
        inbox_id: parseInt(getRl(ctx, i, 'uniboxSendInboxId'), 10),
        to_email: ctx.getNodeParameter('uniboxToEmail', i, ''),
        subject: ctx.getNodeParameter('uniboxSubject', i, ''),
        body: ctx.getNodeParameter('uniboxBody', i, ''),
        ...sendExtra,
      };
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'POST',
        path: '/api/unibox/send',
        body,
      })) as IDataObject;
    }
    default:
      throw new Error(`Unknown unibox operation: ${operation}`);
  }
}

async function runEmailAccount(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  operation: string,
): Promise<IDataObject> {
  switch (operation) {
    case 'gmailStatus':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/gmail/status',
      })) as IDataObject;
    case 'gmailListAccounts':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/gmail/accounts',
      })) as IDataObject;
    case 'gmailDisconnect': {
      const id = getRl(ctx, i, 'gmailAccountId');
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/gmail/accounts/${id}`,
      });
      return { success: true };
    }
    case 'gmailPermissions':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/gmail/permissions',
      })) as IDataObject;
    case 'office365Status':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/office365/status',
      })) as IDataObject;
    case 'office365ListAccounts':
      return (await quicklyRequest.call(ctx, credentials, {
        method: 'GET',
        path: '/api/office365/accounts',
      })) as IDataObject;
    case 'office365Disconnect': {
      const id = getRl(ctx, i, 'office365AccountId');
      await quicklyRequest.call(ctx, credentials, {
        method: 'DELETE',
        path: `/api/office365/accounts/${id}`,
      });
      return { success: true };
    }
    default:
      throw new Error(`Unknown email account operation: ${operation}`);
  }
}

async function dispatch(
  ctx: IExecuteFunctions,
  i: number,
  credentials: IDataObject,
  resource: string,
  operation: string,
): Promise<IDataObject | INodeExecutionData> {
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

export async function executeQuickly(
  this: IExecuteFunctions,
): Promise<INodeExecutionData[][]> {
  const items = this.getInputData();
  const returnData: INodeExecutionData[] = [];
  const credentials = await this.getCredentials('quicklyApi');
  for (let i = 0; i < items.length; i++) {
    try {
      const resource = this.getNodeParameter('resource', i) as string;
      const operation = this.getNodeParameter('operation', i) as string;
      if (!operation) {
        throw new Error('Select an operation');
      }
      const result = await dispatch(this, i, credentials as IDataObject, resource, operation);
      if (
        result &&
        typeof result === 'object' &&
        'pairedItem' in result &&
        'binary' in result
      ) {
        returnData.push(result as INodeExecutionData);
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
      const data = result as IDataObject;
      returnData.push({
        json: data !== null && typeof data === 'object' ? data : { result: data },
        pairedItem: { item: i },
      });
    } catch (error) {
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
