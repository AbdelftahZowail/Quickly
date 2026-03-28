import type { ICredentialType, INodeProperties, Icon, ICredentialTestRequest, IAuthenticate } from 'n8n-workflow';
export declare class QuicklyApi implements ICredentialType {
    name: string;
    displayName: string;
    icon: Icon;
    documentationUrl: string;
    /**
     * Programmatic auth avoids n8n expression bugs (regex in {{ }} caused "invalid syntax"
     * in the connection test) and sets either X-API-Key or Authorization correctly.
     */
    authenticate: IAuthenticate;
    /** baseURL must not use regex inside {{ }} — n8n’s expression parser rejects it. */
    test: ICredentialTestRequest;
    properties: INodeProperties[];
}
