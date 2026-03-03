import { createContext, useContext, useEffect, useState } from 'react';
import { api } from '../api';

const AppModeContext = createContext({ mode: 'development' });

export function AppModeProvider({ children }) {
  const [mode, setMode] = useState('development');

  useEffect(() => {
    api.get('/status')
      .then(data => setMode(data.app_mode || 'development'))
      .catch(() => {});
  }, []);

  return (
    <AppModeContext.Provider value={{ mode, isProduction: mode === 'production' }}>
      {children}
    </AppModeContext.Provider>
  );
}

export function useAppMode() {
  return useContext(AppModeContext);
}
