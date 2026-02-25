import { createContext, useContext, useState, useCallback } from 'react';

const NotificationContext = createContext(null);

export function NotificationProvider({ children }) {
  const [notes, setNotes] = useState([]);
  const add = useCallback((note) => {
    const id = Date.now();
    setNotes(n => [...n, { id, ...note }]);
    setTimeout(() => setNotes(n => n.filter(x => x.id !== id)), note.duration || 4000);
  }, []);
  return (
    <NotificationContext.Provider value={add}>
      {children}
      <div className="fixed top-4 right-4 space-y-2 z-50">
        {notes.map(n => (
          <div key={n.id} className={`px-4 py-2 rounded shadow ${n.type === 'error' ? 'bg-red-500 text-white' : 'bg-green-500 text-white'}`}>
            {n.message}
          </div>
        ))}
      </div>
    </NotificationContext.Provider>
  );
}

export function useNotify() {
  const add = useContext(NotificationContext);
  if (!add) throw new Error('useNotify must be used within NotificationProvider');
  return add;
}
