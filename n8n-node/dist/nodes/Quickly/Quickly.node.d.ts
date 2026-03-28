import type { IExecuteFunctions, INodeExecutionData, INodeType, INodeTypeDescription } from 'n8n-workflow';
import * as listSearch from './listSearch';
import * as loaders from './loadOptions';
export declare class Quickly implements INodeType {
    description: INodeTypeDescription;
    methods: {
        loadOptions: {
            loadOperations: typeof loaders.loadOperations;
            getInboxes: typeof loaders.getInboxes;
            getLeads: typeof loaders.getLeads;
            getWebhookEventTypes: typeof loaders.getWebhookEventTypes;
        };
        listSearch: {
            searchCampaigns: typeof listSearch.searchCampaigns;
            searchInboxes: typeof listSearch.searchInboxes;
            searchLeads: typeof listSearch.searchLeads;
            searchSequencesForCampaign: typeof listSearch.searchSequencesForCampaign;
            searchVariantsForSequence: typeof listSearch.searchVariantsForSequence;
            searchLeadsForCampaign: typeof listSearch.searchLeadsForCampaign;
            searchWebhooks: typeof listSearch.searchWebhooks;
            searchKnownIps: typeof listSearch.searchKnownIps;
            searchAiFeatures: typeof listSearch.searchAiFeatures;
            searchGmailAccounts: typeof listSearch.searchGmailAccounts;
            searchOffice365Accounts: typeof listSearch.searchOffice365Accounts;
            searchUniboxThreads: typeof listSearch.searchUniboxThreads;
            searchScheduleSentLogs: typeof listSearch.searchScheduleSentLogs;
        };
    };
    execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]>;
}
