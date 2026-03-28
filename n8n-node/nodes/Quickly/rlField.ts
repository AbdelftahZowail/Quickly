import type {
  INodeProperties,
  INodePropertyModeTypeOptions,
  NodeParameterValueType,
} from 'n8n-workflow';
import { RL_DEFAULT } from './rlUtils';

/**
 * Single-select resource: searchable list + "By ID" string (expressions supported in ID mode).
 * List mode always depends on `resource` and `operation` so the UI reloads when either changes
 * (see n8n `loadOptionsDependsOn` on RLC list mode). Pass `listExtraDepends` for parents
 * (e.g. `campaignId` before loading sequences).
 */
export function resourceLocatorField(
  displayName: string,
  name: string,
  searchListMethod: string,
  placeholder: string,
  displayOptions: INodeProperties['displayOptions'],
  required?: boolean,
  description?: string,
  /** Pre-selected list value (e.g. single known option like reply_classifier). */
  defaultListValue?: string,
  /** Additional node parameter names that should trigger a list reload. */
  listExtraDepends?: string[],
): INodeProperties {
  const defaultParam: NodeParameterValueType =
    defaultListValue !== undefined
      ? ({ __rl: true, mode: 'list', value: defaultListValue } as unknown as NodeParameterValueType)
      : RL_DEFAULT;
  const loadOptionsDependsOn = Array.from(
    new Set(['resource', 'operation', ...(listExtraDepends ?? [])]),
  );
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
        } as INodePropertyModeTypeOptions,
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
