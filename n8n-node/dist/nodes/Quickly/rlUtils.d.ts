import type { IExecuteFunctions, NodeParameterValueType } from 'n8n-workflow';
/** Normalize stored parameter from a resourceLocator (or legacy plain string). */
export declare function getResourceLocatorValue(raw: unknown): string;
export declare function getRl(ctx: IExecuteFunctions, itemIndex: number, name: string, fallback?: string): string;
/** Default object for new resourceLocator parameters in node definitions. */
export declare const RL_DEFAULT: NodeParameterValueType;
