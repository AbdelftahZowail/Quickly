"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.resourceLocatorField = resourceLocatorField;
const rlUtils_1 = require("./rlUtils");
/**
 * Single-select resource: searchable list + "By ID" string (expressions supported in ID mode).
 * List mode always depends on `resource` and `operation` so the UI reloads when either changes
 * (see n8n `loadOptionsDependsOn` on RLC list mode). Pass `listExtraDepends` for parents
 * (e.g. `campaignId` before loading sequences).
 */
function resourceLocatorField(displayName, name, searchListMethod, placeholder, displayOptions, required, description, 
/** Pre-selected list value (e.g. single known option like reply_classifier). */
defaultListValue, 
/** Additional node parameter names that should trigger a list reload. */
listExtraDepends) {
    const defaultParam = defaultListValue !== undefined
        ? { __rl: true, mode: 'list', value: defaultListValue }
        : rlUtils_1.RL_DEFAULT;
    const loadOptionsDependsOn = Array.from(new Set(['resource', 'operation', ...(listExtraDepends !== null && listExtraDepends !== void 0 ? listExtraDepends : [])]));
    return {
        displayName,
        name,
        type: 'resourceLocator',
        default: defaultParam,
        required: required === true,
        modes: [
            {
                displayName: 'From List',
                name: 'list',
                type: 'list',
                // loadOptionsDependsOn is supported on RLC list mode in the editor (n8n PR #6101);
                // older n8n-workflow typings omit it on INodePropertyModeTypeOptions.
                typeOptions: {
                    searchListMethod,
                    searchable: true,
                    loadOptionsDependsOn,
                },
            },
            {
                displayName: 'By ID',
                name: 'id',
                type: 'string',
                placeholder,
            },
        ],
        displayOptions,
        description,
    };
}
