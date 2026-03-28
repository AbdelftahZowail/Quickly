import type { INodePropertyOptions } from 'n8n-workflow';
import { accountOperations } from './account.operations';
import { campaignOperations } from './campaigns.operations';
import { campaignLeadOperations } from './campaignLeads.operations';
import { emailAccountOperations } from './emailAccounts.operations';
import { inboxOperations } from './inboxes.operations';
import { leadOperations } from './leads.operations';
import { notificationOperations } from './notifications.operations';
import { scheduleOperations } from './schedule.operations';
import { sequenceVariantOperations } from './sequenceVariants.operations';
import { sequenceOperations } from './sequences.operations';
import { settingsOperations } from './settings.operations';
import { statusOperations } from './status.operations';
import { uniboxOperations } from './unibox.operations';
import { webhookOperations } from './webhooks.operations';

export const RESOURCE_OPTIONS: INodePropertyOptions[] = [
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

export const OPERATIONS_BY_RESOURCE: Record<string, INodePropertyOptions[]> = {
  account: accountOperations,
  campaign: campaignOperations,
  sequence: sequenceOperations,
  sequenceVariant: sequenceVariantOperations,
  lead: leadOperations,
  campaignLead: campaignLeadOperations,
  inbox: inboxOperations,
  schedule: scheduleOperations,
  status: statusOperations,
  settings: settingsOperations,
  webhook: webhookOperations,
  notification: notificationOperations,
  unibox: uniboxOperations,
  emailAccount: emailAccountOperations,
};
