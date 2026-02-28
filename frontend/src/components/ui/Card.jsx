import React from 'react';
import classNames from 'classnames';

/**
 * Simple white card container with padding and rounded corners.
 * Additional className will be merged.
 */
export function Card({ className, children, ...props }) {
  return (
    <div
      className={classNames('bg-white rounded-lg shadow p-4', className)}
      {...props}
    >
      {children}
    </div>
  );
}

// also provide default export for backwards compatibility
export default Card;
