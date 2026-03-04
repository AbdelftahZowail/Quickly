import { createContext, useContext, useState, useCallback, useEffect } from 'react';
import { Button } from '../components/ui/Button';

const ConfirmContext = createContext(null);

export function ConfirmProvider({ children }) {
  const [dialog, setDialog] = useState({ visible: false, message: '', resolve: null });

  const confirm = useCallback((message) => {
    return new Promise((res) => {
      setDialog({ visible: true, message, resolve: res });
    });
  }, []);

  const handleResult = (result) => {
    if (dialog.resolve) dialog.resolve(result);
    setDialog({ visible: false, message: '', resolve: null });
  };

  // Escape key = cancel
  useEffect(() => {
    if (!dialog.visible) return;
    const onKey = (e) => { if (e.key === 'Escape') handleResult(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [dialog.visible]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {dialog.visible && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div
            data-darkreader-ignore
            className="p-6 rounded-xl shadow-lg w-full max-w-sm mx-auto"
            style={{ backgroundColor: 'white' }}
          >
            <p className="mb-4 break-words text-gray-800">{dialog.message}</p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => handleResult(false)}>
                Cancel
              </Button>
              <Button variant="default" size="sm" onClick={() => handleResult(true)}>
                OK
              </Button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const confirm = useContext(ConfirmContext);
  if (!confirm) throw new Error('useConfirm must be used within ConfirmProvider');
  return confirm;
}
