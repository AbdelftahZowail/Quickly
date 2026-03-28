import * as React from 'react';
import { cn } from '../../utils/cn';

const sizeClasses = {
  sm: 'h-8 min-h-[2rem] px-3 gap-1.5',
  default: 'min-h-10 px-4 py-2 gap-2',
  full: 'w-full min-h-[2.75rem] px-4 py-3 gap-2 justify-center text-center',
};

/**
 * Clickable dashed-border upload control: native file picker + optional drag-and-drop.
 * Forwards ref to the underlying file input element.
 */
const FileUploadArea = React.forwardRef(function FileUploadArea(
  { accept, disabled, onChange, className, size = 'default', children },
  ref,
) {
  const innerRef = React.useRef(null);
  const [dragOver, setDragOver] = React.useState(false);

  const setInputRef = React.useCallback(
    (node) => {
      innerRef.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref != null) ref.current = node;
    },
    [ref],
  );

  const attachFileAndNotify = React.useCallback((file) => {
    const input = innerRef.current;
    if (!input || !file) return;
    try {
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    } catch {
      /* ignore unsupported environments */
    }
  }, []);

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const f = e.dataTransfer.files?.[0];
    if (f) attachFileAndNotify(f);
  };

  return (
    <label
      className={cn(
        'inline-flex cursor-pointer items-center justify-center rounded-md text-sm font-medium transition-colors',
        'border-2 border-dashed border-primary/55 text-primary hover:bg-primary/10 hover:border-primary',
        'focus-within:outline-none focus-within:ring-2 focus-within:ring-teal-500 focus-within:ring-offset-2 ring-offset-background',
        'dark:border-primary/50 dark:hover:bg-primary/10',
        sizeClasses[size],
        dragOver && 'bg-primary/15 border-primary',
        disabled && 'cursor-not-allowed opacity-50 pointer-events-none',
        className,
      )}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragOver(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setDragOver(false);
      }}
      onDrop={onDrop}
    >
      <input
        ref={setInputRef}
        type="file"
        accept={accept}
        disabled={disabled}
        className="sr-only"
        onChange={onChange}
      />
      {children}
    </label>
  );
});

FileUploadArea.displayName = 'FileUploadArea';

export { FileUploadArea };
