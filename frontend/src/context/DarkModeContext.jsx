import { createContext, useState, useEffect, useContext, useCallback } from 'react';
import { enable, disable } from 'darkreader';

const STORAGE_KEY = 'darkreader';

// central configuration for dark mode appearance
const DARKREADER_SETTINGS = { brightness: 150, contrast: 100, sepia: 0 };

/** @returns {'system' | 'dark' | 'light'} */
function readDarkThemePreference() {
  try {
    const s = localStorage.getItem(STORAGE_KEY);
    if (s === 'on' || s === 'dark') return 'dark';
    if (s === 'off' || s === 'light') return 'light';
    return 'system';
  } catch {
    return 'system';
  }
}

function systemPrefersDark() {
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Effective dark state for a stored preference. */
function effectiveDarkForPreference(pref) {
  if (pref === 'dark') return true;
  if (pref === 'light') return false;
  return systemPrefersDark();
}

const DarkModeContext = createContext({
  darkMode: false,
  themePreference: 'system',
  setThemePreference: () => {}
});

export function DarkModeProvider({ children }) {
  const [themePreference, setThemePreferenceState] = useState(readDarkThemePreference);
  const [darkMode, setDarkMode] = useState(() =>
    typeof window !== 'undefined' ? effectiveDarkForPreference(readDarkThemePreference()) : false
  );

  const applyRootClass = on => {
    const root = document.documentElement;
    if (on) root.classList.add('dark');
    else root.classList.remove('dark');
  };

  const applyEffectiveDark = useCallback(on => {
    if (on) {
      enable(DARKREADER_SETTINGS);
      applyRootClass(true);
    } else {
      disable();
      applyRootClass(false);
    }
    setDarkMode(on);
  }, []);

  const setThemePreference = useCallback(pref => {
    if (pref !== 'system' && pref !== 'dark' && pref !== 'light') return;
    setThemePreferenceState(pref);
    localStorage.setItem(
      STORAGE_KEY,
      pref === 'dark' ? 'dark' : pref === 'light' ? 'light' : 'system'
    );
  }, []);

  useEffect(() => {
    const on = effectiveDarkForPreference(themePreference);
    applyEffectiveDark(on);

    if (themePreference !== 'system') return undefined;

    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => {
      applyEffectiveDark(mq.matches);
    };
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [themePreference, applyEffectiveDark]);

  return (
    <DarkModeContext.Provider value={{ darkMode, themePreference, setThemePreference }}>
      {children}
    </DarkModeContext.Provider>
  );
}

export function useDarkMode() {
  return useContext(DarkModeContext);
}
