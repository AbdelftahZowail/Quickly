import { createContext, useState, useEffect, useContext } from 'react';
import { enable, disable, isEnabled } from 'darkreader';

// central configuration for dark mode appearance
const DARKREADER_SETTINGS = { brightness: 150, contrast: 100, sepia: 0 };

const DarkModeContext = createContext({
  darkMode: false,
  toggleDarkMode: () => {}
});

export function DarkModeProvider({ children }) {
  const [darkMode, setDarkMode] = useState(false);

  // helper for applying the tailwind "dark" class to the document root
  const applyRootClass = on => {
    const root = document.documentElement;
    if (on) root.classList.add('dark');
    else root.classList.remove('dark');
  };

  // run once on mount to apply stored preference
  useEffect(() => {
    const stored = localStorage.getItem('darkreader');
    const currently = isEnabled();
    if (stored === 'on' && !currently) {
      // raise contrast and slightly dim brightness for readability
      enable(DARKREADER_SETTINGS);
      setDarkMode(true);
      applyRootClass(true);
    } else if (stored === 'off' && currently) {
      disable();
      setDarkMode(false);
      applyRootClass(false);
    } else {
      setDarkMode(currently);
      applyRootClass(currently);
    }
  }, []);

  const toggleDarkMode = () => {
    if (darkMode) {
      disable();
      setDarkMode(false);
      applyRootClass(false);
      localStorage.setItem('darkreader', 'off');
    } else {
      // match the same contrast settings used on mount
      enable(DARKREADER_SETTINGS);
      setDarkMode(true);
      applyRootClass(true);
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
