import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from '../api';

const NotificationsContext = createContext({ count: 0, refresh: () => {} });

/**
 * Provides the global unread notification count for the notification center.
 *
 * The count is refreshed:
 *  - on mount
 *  - whenever the window regains focus (so switching back from another tab/page updates it)
 *  - every 30 seconds as a fallback
 *  - manually via the `refresh` function returned by the hook
 */
export function NotificationsProvider({ children }) {
  const [count, setCount] = useState(0);
  const timerRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get('/notifications/unread-count');
      setCount(Number(data?.unread ?? 0));
    } catch {
      // ignore fetch errors – badge count is non-critical
    }
  }, []);

  // Polling fallback every 30 s.
  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, 30_000);
    return () => clearInterval(timerRef.current);
  }, [refresh]);

  // Refresh when the window regains focus (e.g. user switches back from Notifications page).
  useEffect(() => {
    const onFocus = () => refresh();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, [refresh]);

  return (
    <NotificationsContext.Provider value={{ count, refresh }}>
      {children}
    </NotificationsContext.Provider>
  );
}

export function useNotifications() {
  return useContext(NotificationsContext);
}
