import { createContext, useContext, useState, useCallback, useMemo } from 'react';

const LoadingContext = createContext(null);

export function LoadingProvider({ children }) {
  const [count, setCount] = useState(0);
  const start = useCallback(() => setCount(c => c + 1), []);
  const stop = useCallback(() => setCount(c => Math.max(0, c - 1)), []);

  // memoize the context value so consumers don't get a new object on each render
  const value = useMemo(() => ({ start, stop }), [start, stop]);

  return (
    <LoadingContext.Provider value={value}>
      {children}
      {count > 0 && (
        <div className="fixed inset-0 flex items-center justify-center z-50">
          <div className="loader"></div>
        </div>
      )}
    </LoadingContext.Provider>
  );
}

export function useLoading() {
  const ctx = useContext(LoadingContext);
  if (!ctx) throw new Error('useLoading must be used within LoadingProvider');
  return ctx;
}
