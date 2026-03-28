import type {
  IDataObject,
  ILoadOptionsFunctions,
  INodeListSearchItems,
  INodeListSearchResult,
} from 'n8n-workflow';
import { getResourceLocatorValue } from './rlUtils';
import { quicklyRequest } from './transport';

function currentRl(ctx: ILoadOptionsFunctions, param: string): string {
  try {
    return getResourceLocatorValue(ctx.getCurrentNodeParameter(param));
  } catch {
    return '';
  }
}

function applyFilter(items: INodeListSearchItems[], filter?: string): INodeListSearchItems[] {
  const f = (filter || '').trim().toLowerCase();
  if (!f) return items.slice(0, 100);
  return items
    .filter(
      (it) =>
        String(it.name || '')
          .toLowerCase()
          .includes(f) ||
        String(it.value || '')
          .toLowerCase()
          .includes(f) ||
        String(it.description || '')
          .toLowerCase()
          .includes(f),
    )
    .slice(0, 100);
}

export async function searchCampaigns(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/campaigns',
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.slice(0, 500).map((c) => ({
      name: `${String(c.name ?? '')} (#${c.id})`,
      value: String(c.id),
      description: `Leads: ${(c.stats as IDataObject)?.total_leads ?? '—'} · Sent: ${(c.stats as IDataObject)?.emails_sent ?? '—'}`,
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchInboxes(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/inboxes',
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.slice(0, 500).map((r) => ({
      name: `${String(r.display_name || r.email || 'Inbox')} (#${r.id})`,
      value: String(r.id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchLeads(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/leads',
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.slice(0, 500).map((r) => ({
      name: `${String(r.email ?? r.id)} (#${r.id})`,
      value: String(r.id),
      description: String(r.name || ''),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchSequencesForCampaign(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const campaignId = currentRl(this, 'campaignId');
  if (!campaignId) return { results: [] };
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: `/api/campaigns/${campaignId}/sequences`,
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.map((s, idx) => ({
      name: `Step ${(s.position as number) ?? idx + 1} — ${String(s.subject || '(no subject)')} (#${s.id})`,
      value: String(s.id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchVariantsForSequence(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const campaignId = currentRl(this, 'campaignId');
  const sequenceId = currentRl(this, 'sequenceId');
  if (!campaignId || !sequenceId) return { results: [] };
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: `/api/campaigns/${campaignId}/sequences/${sequenceId}/variants`,
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = [
      { name: '— Default sequence content —', value: '__none__' },
      ...rows.map((v) => ({
        name: `${String(v.label || 'Variant')} (#${v.id})`,
        value: String(v.id),
      })),
    ];
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchLeadsForCampaign(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const campaignId = currentRl(this, 'campaignId');
  if (!campaignId) return { results: [] };
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: `/api/campaigns/${campaignId}/leads`,
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.slice(0, 500).map((r) => ({
      name: `${String(r.email)} (#${r.lead_id})`,
      value: String(r.lead_id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchWebhooks(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/settings/webhooks',
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.map((w) => ({
      name: `${String(w.description || w.url)} (#${w.id})`,
      value: String(w.id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchKnownIps(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const data = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/settings/known-ips',
    })) as IDataObject;
    const rows = data.known_ips as IDataObject[] | undefined;
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.map((r) => ({
      name: `${String(r.ip_address)} (#${r.id})`,
      value: String(r.id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchAiFeatures(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const results: INodeListSearchItems[] = [
    {
      name: 'Reply Interest Classifier',
      value: 'reply_classifier',
    },
  ];
  return { results: applyFilter(results, filter) };
}

export async function searchGmailAccounts(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/gmail/accounts',
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.map((a) => ({
      name: `${String(a.google_email || a.inbox_email)} (#${a.id})`,
      value: String(a.id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchOffice365Accounts(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/office365/accounts',
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.map((a) => ({
      name: `${String(a.microsoft_email)} (#${a.id})`,
      value: String(a.id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchUniboxThreads(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const data = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/unibox',
      qs: { page: 1, page_size: 100, leads_only: false },
    })) as IDataObject;
    const items = data.items as IDataObject[] | undefined;
    if (!Array.isArray(items)) return { results: [] };
    const results: INodeListSearchItems[] = items.map((it) => {
      const tid = String(it.thread_id ?? '');
      const iid = String(it.inbox_id ?? '');
      const sub = String(it.subject ?? '(no subject)');
      return {
        name: `${sub.slice(0, 60)}${sub.length > 60 ? '…' : ''} [inbox ${iid}]`,
        value: `${iid}||${tid}`,
        description: String(it.lead_email || it.inbox_account || ''),
      };
    });
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}

export async function searchScheduleSentLogs(
  this: ILoadOptionsFunctions,
  filter?: string,
): Promise<INodeListSearchResult> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/schedule/sent',
      qs: { limit: 200, offset: 0, include_events: false, include_body: false },
    })) as IDataObject[];
    if (!Array.isArray(rows)) return { results: [] };
    const results: INodeListSearchItems[] = rows.slice(0, 200).map((r) => ({
      name: `#${r.log_id} — ${String(r.lead_email)} — ${String(r.subject || '').slice(0, 40)}`,
      value: String(r.log_id),
    }));
    return { results: applyFilter(results, filter) };
  } catch {
    return { results: [] };
  }
}
