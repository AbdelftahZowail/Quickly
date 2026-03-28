"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.Quickly = void 0;
const n8n_workflow_1 = require("n8n-workflow");
const account_operations_1 = require("./account.operations");
const campaigns_operations_1 = require("./campaigns.operations");
const campaignLeads_operations_1 = require("./campaignLeads.operations");
const emailAccounts_operations_1 = require("./emailAccounts.operations");
const executeRouter_1 = require("./executeRouter");
const inboxes_operations_1 = require("./inboxes.operations");
const listSearch = __importStar(require("./listSearch"));
const loaders = __importStar(require("./loadOptions"));
const leads_operations_1 = require("./leads.operations");
const notifications_operations_1 = require("./notifications.operations");
const operationsCatalog_1 = require("./operationsCatalog");
const schedule_operations_1 = require("./schedule.operations");
const sequenceVariants_operations_1 = require("./sequenceVariants.operations");
const sequences_operations_1 = require("./sequences.operations");
const settings_operations_1 = require("./settings.operations");
const status_operations_1 = require("./status.operations");
const unibox_operations_1 = require("./unibox.operations");
const webhooks_operations_1 = require("./webhooks.operations");
class Quickly {
    constructor() {
        this.description = {
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
            inputs: [n8n_workflow_1.NodeConnectionTypes.Main],
            outputs: [n8n_workflow_1.NodeConnectionTypes.Main],
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
                    options: operationsCatalog_1.RESOURCE_OPTIONS,
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
                ...account_operations_1.accountFields,
                ...campaigns_operations_1.campaignFields,
                ...sequences_operations_1.sequenceFields,
                ...sequenceVariants_operations_1.sequenceVariantFields,
                ...leads_operations_1.leadFields,
                ...campaignLeads_operations_1.campaignLeadFields,
                ...inboxes_operations_1.inboxFields,
                ...schedule_operations_1.scheduleFields,
                ...status_operations_1.statusFields,
                ...settings_operations_1.settingsFields,
                ...webhooks_operations_1.webhookFields,
                ...notifications_operations_1.notificationFields,
                ...unibox_operations_1.uniboxFields,
                ...emailAccounts_operations_1.emailAccountFields,
            ],
        };
        this.methods = {
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
    }
    async execute() {
        return await executeRouter_1.executeQuickly.call(this);
    }
}
exports.Quickly = Quickly;
