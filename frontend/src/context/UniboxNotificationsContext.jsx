import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

const UniboxNotificationsContext = createContext({ count: 0, refresh: () => {} });

/**
 * Provides the global unread-lead-reply notification count.
 *
 * The count is refreshed:
 *  - on mount
 *  - whenever an SSE `unibox.notification` or `unibox.notification.count` event arrives
 *  - every 60 seconds as a fallback
 */
export function UniboxNotificationsProvider({ children }) {
  const [count, setCount] = useState(0);
  const timerRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch('/api/unibox/notifications');
      if (!res.ok) return;
      const data = await res.json();
      setCount(Number(data?.count ?? 0));
    } catch {
      // ignore fetch errors – badge count is non-critical
    }
  }, []);

  // Polling fallback every 60 s.
  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, 60_000);
    return () => clearInterval(timerRef.current);
  }, [refresh]);

  // Real-time updates via the existing unibox SSE stream.
  useEffect(() => {
    const source = new EventSource('/api/unibox/events');

    const onNotification = (evt) => {
      try {
        const data = JSON.parse(evt.data || '{}');
        // If the server included the new count directly, use it; otherwise refresh.
        if (typeof data?.count === 'number') {
          setCount(data.count);
        } else {
          refresh();
        }
      } catch {
        refresh();
      }
    };

    // unibox.notification fires when a new lead reply arrives.
    source.addEventListener('unibox.notification', onNotification);
    // unibox.notification.count fires when a read action clears a notification.
    source.addEventListener('unibox.notification.count', onNotification);

    source.onerror = () => {
      // Let the browser auto-reconnect; count will sync on next event.
    };

    return () => {
      source.removeEventListener('unibox.notification', onNotification);
      source.removeEventListener('unibox.notification.count', onNotification);
      source.close();
    };
  }, [refresh]);

  return (
    <UniboxNotificationsContext.Provider value={{ count, refresh }}>
      {children}
    </UniboxNotificationsContext.Provider>
  );
}

export function useUniboxNotifications() {
  return useContext(UniboxNotificationsContext);
}
