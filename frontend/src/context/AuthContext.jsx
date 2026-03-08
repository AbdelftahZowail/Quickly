import { createContext, useContext, useState, useEffect, useCallback } from 'react';

const AuthContext = createContext(null);

// Access token lifetime in minutes – must match ACCESS_TOKEN_EXPIRE_MINUTES in app/auth.py.
// We refresh 5 minutes before expiry.
const ACCESS_TOKEN_EXPIRE_MINUTES = 30;
const AUTO_REFRESH_INTERVAL_MS = (ACCESS_TOKEN_EXPIRE_MINUTES - 5) * 60 * 1000;

const AuthContext = createContext(null);

// Store the access token in memory only (not localStorage) to prevent XSS access
let _accessToken = null;

export function getAccessToken() {
  return _accessToken;
}

export function setAccessToken(token) {
  _accessToken = token;
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [setupComplete, setSetupComplete] = useState(null);

  const refreshToken = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        credentials: 'include', // send httpOnly refresh cookie
      });
      if (res.ok) {
        const data = await res.json();
        _accessToken = data.access_token;
        // Re-fetch user with new token
        const userRes = await fetch('/api/auth/me', {
          headers: { Authorization: `Bearer ${_accessToken}` },
        });
        if (userRes.ok) {
          setUser(await userRes.json());
          return true;
        }
      }
    } catch { /* ignore */ }
    _accessToken = null;
    setUser(null);
    return false;
  }, []);

  const fetchUser = useCallback(async () => {
    if (!_accessToken) {
      setLoading(false);
      return;
    }
    try {
      const res = await fetch('/api/auth/me', {
        headers: { Authorization: `Bearer ${_accessToken}` },
      });
      if (res.ok) {
        const data = await res.json();
        setUser(data);
      } else {
        // Token might be expired, try refresh
        const refreshed = await refreshToken();
        if (!refreshed) {
          _accessToken = null;
          setUser(null);
        }
      }
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, [refreshToken]);

  const login = useCallback(async (username, password) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(err.detail || 'Login failed');
    }
    const data = await res.json();
    _accessToken = data.access_token;
    await fetchUser();
    return data;
  }, [fetchUser]);

  const register = useCallback(async (username, email, password) => {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Registration failed' }));
      throw new Error(err.detail || 'Registration failed');
    }
    return await res.json();
  }, []);

  const logout = useCallback(async () => {
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
    }).catch(() => {});
    _accessToken = null;
    setUser(null);
  }, []);

  const checkSetup = useCallback(async () => {
    try {
      const res = await fetch('/api/auth/setup-status');
      const data = await res.json();
      setSetupComplete(data.setup_complete);
      return data.setup_complete;
    } catch {
      return null;
    }
  }, []);

  // On mount: check setup status, try refresh from httpOnly cookie
  useEffect(() => {
    (async () => {
      await checkSetup();
      const refreshed = await refreshToken();
      if (!refreshed) {
        setLoading(false);
      }
    })();
  }, [checkSetup, refreshToken]);

  // Auto-refresh access token before expiry (every 25 minutes)
  useEffect(() => {
    if (!user) return;
    const interval = setInterval(() => {
      refreshToken();
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [user, refreshToken]);

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      setupComplete,
      login,
      register,
      logout,
      refreshToken,
      checkSetup,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
