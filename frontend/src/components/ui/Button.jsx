import * as React from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '../../utils/cn';

const buttonVariants = cva(
  'inline-flex items-center justify-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:opacity-50 disabled:pointer-events-none ring-offset-background',
  {
    variants: {
      variant: {
        default: 'bg-primary text-white hover:bg-primary/90',
        destructive: 'bg-red-500 text-white hover:bg-red-600',
        outline: 'border border-primary text-primary hover:bg-primary/10',
        secondary: 'bg-gray-100 text-gray-800 hover:bg-gray-200',
        ghost: 'bg-transparent hover:bg-primary/10 text-primary',
        link: 'underline-offset-4 hover:underline text-primary',
      },
      size: {
        default: 'h-10 px-4',
        sm: 'h-8 px-3',
        lg: 'h-11 px-8',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

// props are forwarded to the underlying button element; variant/size are accepted
const Button = React.forwardRef(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        className={cn(buttonVariants({ variant, size, class: className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';

export { Button, buttonVariants };
