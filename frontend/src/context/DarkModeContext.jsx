import { createContext, useState, useEffect, useContext } from 'react';
import { enable, disable, isEnabled } from 'darkreader';

const DarkModeContext = createContext({
  darkMode: false,
  toggleDarkMode: () => {}
});

export function DarkModeProvider({ children }) {
  const [darkMode, setDarkMode] = useState(false);

  // run once on mount to apply stored preference
  useEffect(() => {
    const stored = localStorage.getItem('darkreader');
    const currently = isEnabled();
    if (stored === 'on' && !currently) {
      enable({ brightness: 100, contrast: 90, sepia: 0 });
      setDarkMode(true);
    } else if (stored === 'off' && currently) {
      disable();
      setDarkMode(false);
    } else {
      setDarkMode(currently);
    }
  }, []);

  const toggleDarkMode = () => {
    if (darkMode) {
      disable();
      setDarkMode(false);
      localStorage.setItem('darkreader', 'off');
    } else {
      enable({ brightness: 100, contrast: 90, sepia: 0 });
      setDarkMode(true);
      localStorage.setItem('darkreader', 'on');
    }
  };

  return (
    <DarkModeContext.Provider value={{ darkMode, toggleDarkMode }}>
      {children}
    </DarkModeContext.Provider>
  );
}

export function useDarkMode() {
  return useContext(DarkModeContext);
}
