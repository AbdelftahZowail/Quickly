import { twMerge } from 'tailwind-merge';
import classNames from 'classnames';

// simple helper combining classnames and tailwind-merge; this mirrors shadcn's
export function cn(...inputs) {
  return twMerge(classNames(...inputs));
}
