import type {
  IDataObject,
  ILoadOptionsFunctions,
  INodePropertyOptions,
} from 'n8n-workflow';
import { OPERATIONS_BY_RESOURCE } from './operationsCatalog';
import { quicklyRequest } from './transport';

export async function loadOperations(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const resource = this.getCurrentNodeParameter('resource') as string;
  const list = OPERATIONS_BY_RESOURCE[resource];
  if (!list?.length) {
    return [{ name: '— Select a resource first —', value: '' }];
  }
  return list;
}

export async function getCampaigns(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/campaigns',
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.slice(0, 500).map((c) => ({
      name: `${String(c.name ?? '')} (#${c.id})`,
      value: String(c.id),
    }));
  } catch {
    return [{ name: '— Could not load campaigns —', value: '' }];
  }
}

export async function getInboxes(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/inboxes',
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.slice(0, 500).map((r) => ({
      name: `${String(r.display_name || r.email || 'Inbox')} (#${r.id})`,
      value: String(r.id),
    }));
  } catch {
    return [{ name: '— Could not load inboxes —', value: '' }];
  }
}

export async function getLeads(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/leads',
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.slice(0, 500).map((r) => ({
      name: `${String(r.email ?? r.id)} (#${r.id})`,
      value: String(r.id),
      description: String(r.name || ''),
    }));
  } catch {
    return [{ name: '— Could not load leads —', value: '' }];
  }
}

export async function getSequencesForCampaign(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const campaignId = this.getCurrentNodeParameter('campaignId') as string;
  if (!campaignId) {
    return [{ name: '— Select a campaign first —', value: '' }];
  }
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: `/api/campaigns/${campaignId}/sequences`,
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.map((s, idx) => ({
      name: `Step ${(s.position as number) ?? idx + 1} — ${String(s.subject || '(no subject)')} (#${s.id})`,
      value: String(s.id),
    }));
  } catch {
    return [{ name: '— Could not load sequences —', value: '' }];
  }
}

export async function getVariantsForSequence(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const campaignId = this.getCurrentNodeParameter('campaignId') as string;
  const sequenceId = this.getCurrentNodeParameter('sequenceId') as string;
  if (!campaignId || !sequenceId) {
    return [{ name: '— Select campaign and sequence first —', value: '' }];
  }
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: `/api/campaigns/${campaignId}/sequences/${sequenceId}/variants`,
    })) as IDataObject[];
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
  } catch {
    return [{ name: '— Could not load variants —', value: '' }];
  }
}

export async function getLeadsForPreview(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const campaignId = this.getCurrentNodeParameter('campaignId') as string;
  if (!campaignId) {
    return [{ name: '— Select a campaign first —', value: '' }];
  }
  return getLeadsForCampaign.call(this);
}

export async function getLeadsForCampaign(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const campaignId = this.getCurrentNodeParameter('campaignId') as string;
  if (!campaignId) {
    return [{ name: '— Select a campaign first —', value: '' }];
  }
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: `/api/campaigns/${campaignId}/leads`,
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.slice(0, 500).map((r) => ({
      name: `${String(r.email)} (#${r.lead_id})`,
      value: String(r.lead_id),
    }));
  } catch {
    return [{ name: '— Could not load campaign leads —', value: '' }];
  }
}

export async function getWebhooks(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/settings/webhooks',
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.map((w) => ({
      name: `${String(w.description || w.url)} (#${w.id})`,
      value: String(w.id),
    }));
  } catch {
    return [{ name: '— Could not load webhooks —', value: '' }];
  }
}

export async function getWebhookEventTypes(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/settings/webhooks/events',
    })) as string[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.map((e) => ({ name: e, value: e }));
  } catch {
    return [{ name: '— Could not load events —', value: '' }];
  }
}

export async function getKnownIps(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const data = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/settings/known-ips',
    })) as IDataObject;
    const rows = data.known_ips as IDataObject[] | undefined;
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.map((r) => ({
      name: `${String(r.ip_address)} (#${r.id})`,
      value: String(r.id),
    }));
  } catch {
    return [{ name: '— Could not load known IPs —', value: '' }];
  }
}

export async function getAiFeatureIds(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  return [{ name: 'Reply Interest Classifier', value: 'reply_classifier' }];
}

export async function getGmailAccounts(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/gmail/accounts',
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.map((a) => ({
      name: `${String(a.google_email || a.inbox_email)} (#${a.id})`,
      value: String(a.id),
    }));
  } catch {
    return [{ name: '— Could not load Gmail accounts —', value: '' }];
  }
}

export async function getOffice365Accounts(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/office365/accounts',
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.map((a) => ({
      name: `${String(a.microsoft_email)} (#${a.id})`,
      value: String(a.id),
    }));
  } catch {
    return [{ name: '— Could not load Office 365 accounts —', value: '' }];
  }
}

export async function getUniboxThreads(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const data = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/unibox',
      qs: { page: 1, page_size: 100, leads_only: false },
    })) as IDataObject;
    const items = data.items as IDataObject[] | undefined;
    if (!Array.isArray(items)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return items.map((it) => {
      const tid = String(it.thread_id ?? '');
      const iid = String(it.inbox_id ?? '');
      const sub = String(it.subject ?? '(no subject)');
      return {
        name: `${sub.slice(0, 60)}${sub.length > 60 ? '…' : ''} [inbox ${iid}]`,
        value: `${iid}||${tid}`,
        description: String(it.lead_email || it.inbox_account || ''),
      };
    });
  } catch {
    return [{ name: '— Could not load threads —', value: '' }];
  }
}

export async function getScheduleSentLogs(
  this: ILoadOptionsFunctions,
): Promise<INodePropertyOptions[]> {
  const credentials = await this.getCredentials('quicklyApi');
  try {
    const rows = (await quicklyRequest.call(this, credentials as IDataObject, {
      method: 'GET',
      path: '/api/schedule/sent',
      qs: { limit: 200, offset: 0, include_events: false, include_body: false },
    })) as IDataObject[];
    if (!Array.isArray(rows)) {
      return [{ name: '— Invalid response —', value: '' }];
    }
    return rows.slice(0, 200).map((r) => ({
      name: `#${r.log_id} — ${String(r.lead_email)} — ${String(r.subject || '').slice(0, 40)}`,
      value: String(r.log_id),
    }));
  } catch {
    return [{ name: '— Could not load sent logs —', value: '' }];
  }
}
