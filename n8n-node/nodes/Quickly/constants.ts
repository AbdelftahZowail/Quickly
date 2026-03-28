import type { INodePropertyOptions } from 'n8n-workflow';

export const WEEKDAY_OPTIONS: INodePropertyOptions[] = [
  { name: 'Monday', value: '0' },
  { name: 'Tuesday', value: '1' },
  { name: 'Wednesday', value: '2' },
  { name: 'Thursday', value: '3' },
  { name: 'Friday', value: '4' },
  { name: 'Saturday', value: '5' },
  { name: 'Sunday', value: '6' },
];

export const ENROLLMENT_STATUS_OPTIONS: INodePropertyOptions[] = [
  { name: 'Active', value: 'active' },
  { name: 'Contacted', value: 'contacted' },
  { name: 'Completed', value: 'completed' },
  { name: 'Bounced', value: 'bounced' },
  { name: 'Unsubscribed', value: 'unsubscribed' },
  { name: 'Wrong Person', value: 'wrong_person' },
];

export const LEAD_LIST_STATUS_OPTIONS: INodePropertyOptions[] = [
  ...ENROLLMENT_STATUS_OPTIONS,
  { name: 'Invalid (verification / legacy)', value: 'invalid' },
  { name: 'Replied', value: 'replied' },
];

export const INTEREST_FILTER_OPTIONS: INodePropertyOptions[] = [
  { name: 'Interested', value: 'interested' },
  { name: 'Not Interested', value: 'not_interested' },
  { name: 'Out of Office', value: 'out_of_office' },
  { name: 'Auto Reply', value: 'auto_reply' },
  { name: 'Unset / None', value: 'unset' },
];

export const INTEREST_ENROLLMENT_OPTIONS: INodePropertyOptions[] = [
  { name: 'Interested', value: 'interested' },
  { name: 'Not Interested', value: 'not_interested' },
  { name: 'Out of Office', value: 'out_of_office' },
  { name: 'Auto Reply', value: 'auto_reply' },
  { name: 'Clear', value: '' },
];

export const INBOX_PROVIDER_OPTIONS: INodePropertyOptions[] = [
  { name: 'Gmail', value: 'gmail' },
  { name: 'Office 365', value: 'office365' },
];

export const SCHEDULING_STRATEGY_OPTIONS: INodePropertyOptions[] = [
  { name: 'Priority', value: 'priority' },
  { name: 'Round Robin', value: 'round_robin' },
];

export const PAUSE_INBOX_ACTION_OPTIONS: INodePropertyOptions[] = [
  { name: 'Pause Leads', value: 'pause_leads' },
  { name: 'Reassign', value: 'reassign' },
];
