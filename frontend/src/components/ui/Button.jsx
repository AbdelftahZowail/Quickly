import React from 'react';
import classNames from 'classnames';

/**
 * Simple button component with a couple of common variants/sizes.
 *
 * Props:
 *  - variant: "primary" | "secondary" | "danger" | "link" (default "primary")
 *  - size: "sm" | "md" | "lg" (default "md")
 *  - fullWidth: bool
 *  - as: tag to render ("button" or "a")
 *  - className: additional classes
 *  - ...other props passed through
 */
export default function Button({
  variant = 'primary',
  size = 'md',
  fullWidth = false,
  as: Component = 'button',
  className,
  children,
  ...props
}) {
  const base = 'btn';
  const variantClass = {
    primary: 'btn-primary',
    secondary: 'btn-secondary',
    danger: 'btn-danger',
    link: 'btn-link',
  }[variant];

  const sizeClass = {
    sm: 'text-sm px-2 py-1',
    md: 'text-sm px-4 py-2',
    lg: 'text-base px-4 py-2',
  }[size];

  const widthClass = fullWidth ? 'w-full justify-center' : '';

  return (
    <Component
      className={classNames(base, variantClass, sizeClass, widthClass, className)}
      {...props}
    >
      {children}
    </Component>
  );
}
