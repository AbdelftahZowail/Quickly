"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.RL_DEFAULT = void 0;
exports.getResourceLocatorValue = getResourceLocatorValue;
exports.getRl = getRl;
/** Normalize stored parameter from a resourceLocator (or legacy plain string). */
function getResourceLocatorValue(raw) {
    if (raw === null || raw === undefined)
        return '';
    if (typeof raw === 'object' && raw !== null && 'value' in raw) {
        const v = raw.value;
        if (v === null || v === undefined)
            return '';
        return String(v);
    }
    return String(raw);
}
function getRl(ctx, itemIndex, name, fallback = '') {
    try {
        const raw = ctx.getNodeParameter(name, itemIndex, fallback);
        return getResourceLocatorValue(raw);
    }
    catch {
        return fallback;
    }
}
/** Default object for new resourceLocator parameters in node definitions. */
exports.RL_DEFAULT = {
    __rl: true,
    mode: 'list',
    value: '',
};
