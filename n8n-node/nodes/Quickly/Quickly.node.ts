import type {
  IExecuteFunctions,
  INodeExecutionData,
  INodeType,
  INodeTypeDescription,
} from 'n8n-workflow';
import { NodeConnectionTypes } from 'n8n-workflow';
import { accountFields } from './account.operations';
import { campaignFields } from './campaigns.operations';
import { campaignLeadFields } from './campaignLeads.operations';
import { emailAccountFields } from './emailAccounts.operations';
import { executeQuickly } from './executeRouter';
import { inboxFields } from './inboxes.operations';
import * as listSearch from './listSearch';
import * as loaders from './loadOptions';
import { leadFields } from './leads.operations';
import { notificationFields } from './notifications.operations';
import { RESOURCE_OPTIONS } from './operationsCatalog';
import { scheduleFields } from './schedule.operations';
import { sequenceVariantFields } from './sequenceVariants.operations';
import { sequenceFields } from './sequences.operations';
import { settingsFields } from './settings.operations';
import { statusFields } from './status.operations';
import { uniboxFields } from './unibox.operations';
import { webhookFields } from './webhooks.operations';

export class Quickly implements INodeType {
  description: INodeTypeDescription = {
    displayName: 'Quickly',
    name: 'quickly',
    icon: 'fa:paper-plane',
    group: ['transform'],
    version: 1,
    subtitle: '={{$parameter["operation"]}}',
    description: 'Interact with the Quickly email outreach REST API (see docs/API.md)',
    defaults: {
      name: 'Quickly',
    },
    inputs: [NodeConnectionTypes.Main],
    outputs: [NodeConnectionTypes.Main],
    credentials: [
      {
        name: 'quicklyApi',
        required: true,
      },
    ],
    properties: [
      {
        displayName: 'Resource',
        name: 'resource',
        type: 'options',
        noDataExpression: true,
        options: RESOURCE_OPTIONS,
        default: 'campaign',
      },
      {
        displayName: 'Operation',
        name: 'operation',
        type: 'options',
        noDataExpression: true,
        typeOptions: {
          loadOptionsMethod: 'loadOperations',
          loadOptionsDependsOn: ['resource'],
        },
        default: '',
        required: true,
      },
      ...accountFields,
      ...campaignFields,
      ...sequenceFields,
      ...sequenceVariantFields,
      ...leadFields,
      ...campaignLeadFields,
      ...inboxFields,
      ...scheduleFields,
      ...statusFields,
      ...settingsFields,
      ...webhookFields,
      ...notificationFields,
      ...uniboxFields,
      ...emailAccountFields,
    ],
  };

  methods = {
    loadOptions: {
      loadOperations: loaders.loadOperations,
      getInboxes: loaders.getInboxes,
      getLeads: loaders.getLeads,
      getWebhookEventTypes: loaders.getWebhookEventTypes,
    },
    listSearch: {
      searchCampaigns: listSearch.searchCampaigns,
      searchInboxes: listSearch.searchInboxes,
      searchLeads: listSearch.searchLeads,
      searchSequencesForCampaign: listSearch.searchSequencesForCampaign,
      searchVariantsForSequence: listSearch.searchVariantsForSequence,
      searchLeadsForCampaign: listSearch.searchLeadsForCampaign,
      searchWebhooks: listSearch.searchWebhooks,
      searchKnownIps: listSearch.searchKnownIps,
      searchAiFeatures: listSearch.searchAiFeatures,
      searchGmailAccounts: listSearch.searchGmailAccounts,
      searchOffice365Accounts: listSearch.searchOffice365Accounts,
      searchUniboxThreads: listSearch.searchUniboxThreads,
      searchScheduleSentLogs: listSearch.searchScheduleSentLogs,
    },
  };

  async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
    return await executeQuickly.call(this);
  }
}
