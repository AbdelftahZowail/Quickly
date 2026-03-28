import type { INodeProperties } from 'n8n-workflow';
/**
 * Single-select resource: searchable list + "By ID" string (expressions supported in ID mode).
 * List mode always depends on `resource` and `operation` so the UI reloads when either changes
 * (see n8n `loadOptionsDependsOn` on RLC list mode). Pass `listExtraDepends` for parents
 * (e.g. `campaignId` before loading sequences).
 */
export declare function resourceLocatorField(displayName: string, name: string, searchListMethod: string, placeholder: string, displayOptions: INodeProperties['displayOptions'], required?: boolean, description?: string, 
/** Pre-selected list value (e.g. single known option like reply_classifier). */
defaultListValue?: string, 
/** Additional node parameter names that should trigger a list reload. */
listExtraDepends?: string[]): INodeProperties;
