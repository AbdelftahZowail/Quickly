import * as React from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '../../utils/cn';

const inputVariants = cva(
  'flex h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary disabled:cursor-not-allowed disabled:opacity-50',
  {
    variants: {
      variant: {
        default: '',
        destructive: 'border-red-500 text-red-600 placeholder:text-red-400 focus:ring-red-500',
      },
      size: {
        default: 'h-10',
        sm: 'h-9 px-2',
        lg: 'h-11 px-8',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

// props include variant/size, additional label/error props
const Input = React.forwardRef(
  ({ label, error, className, variant, size, ...props }, ref) => {
    return (
      <div className="flex flex-col">
        {label && <label className="text-sm font-medium text-gray-700 mb-1">{label}</label>}
        <input
          className={cn(inputVariants({ variant, size, class: className }))}
          ref={ref}
          {...props}
        />
        {error && <p className="mt-1 text-sm text-red-600">{error}</p>}
      </div>
    );
  }
);
Input.displayName = 'Input';

export { Input, inputVariants };
