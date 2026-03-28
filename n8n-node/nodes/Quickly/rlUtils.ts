import type { IExecuteFunctions, NodeParameterValueType } from 'n8n-workflow';

/** Normalize stored parameter from a resourceLocator (or legacy plain string). */
export function getResourceLocatorValue(raw: unknown): string {
  if (raw === null || raw === undefined) return '';
  if (typeof raw === 'object' && raw !== null && 'value' in raw) {
    const v = (raw as { value: unknown }).value;
    if (v === null || v === undefined) return '';
    return String(v);
  }
  return String(raw);
}

export function getRl(ctx: IExecuteFunctions, itemIndex: number, name: string, fallback = ''): string {
  try {
    const raw = ctx.getNodeParameter(name, itemIndex, fallback);
    return getResourceLocatorValue(raw);
  } catch {
    return fallback;
  }
}

/** Default object for new resourceLocator parameters in node definitions. */
export const RL_DEFAULT: NodeParameterValueType = {
  __rl: true,
  mode: 'list',
  value: '',
} as unknown as NodeParameterValueType;
