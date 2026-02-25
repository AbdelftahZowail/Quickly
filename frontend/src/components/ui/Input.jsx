import React from 'react';

/**
 * A stylized text input with optional label.
 * Props:
 *   label: string
 *   error: string (displayed below)
 *   className: additional classes for input itself
 *   ...props passed to <input>
 */
export default function Input({ label, error, className, ...props }) {
  return (
    <div className="flex flex-col">
      {label && <label className="text-sm font-medium text-gray-700 mb-1">{label}</label>}
      <input
        className={`mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-teal-500 focus:border-teal-500 ${className || ''}`}
        {...props}
      />
      {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
    </div>
  );
}
