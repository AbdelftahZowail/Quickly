import { createContext, useState, useContext, useCallback } from 'react';

const OnboardingContext = createContext({
  showOnboarding: false,
  startOnboarding: () => {},
  completeOnboarding: () => {},
});

export function OnboardingProvider({ children }) {
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try {
      return localStorage.getItem('onboardingCompleted') !== 'true';
    } catch {
      return true;
    }
  });

  const startOnboarding = useCallback(() => setShowOnboarding(true), []);

  const completeOnboarding = useCallback(() => {
    setShowOnboarding(false);
    try {
      localStorage.setItem('onboardingCompleted', 'true');
    } catch {}
  }, []);

  return (
    <OnboardingContext.Provider value={{ showOnboarding, startOnboarding, completeOnboarding }}>
      {children}
    </OnboardingContext.Provider>
  );
}

export function useOnboarding() {
  return useContext(OnboardingContext);
}
