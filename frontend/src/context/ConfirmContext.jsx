import { createContext, useContext, useState, useCallback } from 'react';
import { useDarkMode } from './DarkModeContext';
import Button from '../components/ui/Button';

const ConfirmContext = createContext(null);

export function ConfirmProvider({ children }) {
  const [dialog, setDialog] = useState({ visible: false, message: '', resolve: null });
  const { darkMode } = useDarkMode();

  const confirm = useCallback((message) => {
    return new Promise((res) => {
      setDialog({ visible: true, message, resolve: res });
    });
  }, []);

  const handleResult = (result) => {
    if (dialog.resolve) dialog.resolve(result);
    setDialog({ visible: false, message: '', resolve: null });
  };

  // decide background color explicitly to override any media query
  const dialogBg = darkMode ? '#2d3748' /* gray-800 */ : '#ffffff';

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {dialog.visible && (
        <div data-darkreader-ignore className="fixed inset-0 bg-white bg-opacity-30 dark:bg-black dark:bg-opacity-50 backdrop-blur-sm flex items-center justify-center z-50">
          <div
            data-darkreader-ignore
            className="p-6 rounded-lg shadow-lg max-w-sm"
            style={{ backgroundColor: dialogBg }}
          >
            <p className="mb-4 break-words text-gray-900 dark:text-gray-100" style={{ color: 'inherit' }}>{dialog.message}</p>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" onClick={() => handleResult(false)}>
                Cancel
              </Button>
              <Button variant="primary" size="sm" onClick={() => handleResult(true)}>
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
