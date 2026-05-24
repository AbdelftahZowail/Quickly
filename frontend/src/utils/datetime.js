export const DEFAULT_TIME_ZONE = 'UTC';

export function normalizeTimeZone(tz) {
  return tz || DEFAULT_TIME_ZONE;
}

export function formatDateKey(value, timeZone = DEFAULT_TIME_ZONE) {
  if (!value) return 'unknown';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return 'unknown';
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: normalizeTimeZone(timeZone),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

export function formatTimeParts(value, timeZone = DEFAULT_TIME_ZONE) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return { hours: '00', minutes: '00', seconds: '00' };
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: normalizeTimeZone(timeZone),
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const parts = formatter.formatToParts(date);
  const lookup = Object.fromEntries(parts.map(p => [p.type, p.value]));
  return {
    hours: lookup.hour || '00',
    minutes: lookup.minute || '00',
    seconds: lookup.second || '00',
  };
}

export function formatTimeKey(value, timeZone = DEFAULT_TIME_ZONE, includeSeconds = false) {
  const { hours, minutes, seconds } = formatTimeParts(value, timeZone);
  return includeSeconds ? `${hours}:${minutes}:${seconds}` : `${hours}:${minutes}`;
}

export function formatDateTimeKey(value, timeZone = DEFAULT_TIME_ZONE, includeSeconds = false) {
  if (!value) return '—';
  const dateKey = formatDateKey(value, timeZone);
  if (dateKey === 'unknown') return '—';
  return `${dateKey} ${formatTimeKey(value, timeZone, includeSeconds)}`;
}

export function addDaysToDateKey(dateKey, days = 1) {
  if (!dateKey || dateKey === 'unknown') return 'unknown';
  const [year, month, day] = dateKey.split('-').map(Number);
  if (!year || !month || !day) return 'unknown';
  const base = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
  base.setUTCDate(base.getUTCDate() + days);
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'UTC',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(base);
}
