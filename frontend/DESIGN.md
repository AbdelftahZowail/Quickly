# Quickly frontend design guide

This document describes how the Quickly UI is built today. **New pages and edits should match these patterns** so the product stays visually and behaviorally consistent.

## Stack

- **React** (JSX), **React Router** for navigation
- **Tailwind CSS** with `@tailwindcss/forms`
- **Figtree** variable font (local WOFF2 in `src/assets/fonts/`) — also set as Tailwind `fontFamily.sans`
- **Primary color**: `primary` in `tailwind.config.js` (`#14b8a6`, teal). Use `bg-primary`, `text-primary`, `border-primary`, `ring-primary`, and opacity modifiers like `bg-primary/10` rather than hard‑coding hex values
- **Dark mode**: [Dark Reader](https://darkreader.org/) via `DarkModeContext` toggles inversion; `document.documentElement` gets the `dark` class for Tailwind `dark:` utilities. Prefer pairing light defaults with sensible `dark:` variants on new surfaces (see Settings for examples)
- **Icons**: `react-icons` — **Remix Icon** line set (`react-icons/ri`) matches the sidebar

## App shell

Authenticated routes render inside `Layout`:

- **Main column**: `flex-1 bg-gray-50 text-gray-900` with left margin `ml-44` (expanded sidebar) or `ml-16` (collapsed), from `components/Layout.jsx`
- **Sidebar**: fixed `bg-gray-800 text-gray-300`; active nav uses `text-primary font-semibold bg-gray-700`. Brand wordmark uses `text-primary font-extrabold`
- **Test mode**: when enabled, a full-width `bg-red-100 text-red-800` banner appears above page content

Do not remove the sidebar margin on new pages — content must stay clear of the fixed nav.

## Page layout

**Default page wrapper** (Analytics, Campaigns, most screens):

```jsx
<div className="p-8 space-y-6">
  <h1 className="text-2xl font-semibold mb-4">Page title</h1>
  {/* … */}
</div>
```

**Page title**: `text-2xl font-semibold` (or `font-bold` on long-form pages like Settings). Keep one clear `h1` per route.

**Settings-style long pages**: `flex h-full` with a sticky TOC (`border-r border-gray-200 dark:border-gray-700`), scrollable main column `flex-1 overflow-y-auto p-8 max-w-3xl`, section titles `text-lg font-semibold mb-3 border-b pb-2`, subsections with `h2`/`h3` hierarchy as in `pages/Settings.jsx`.

**Auth (Login)**: centered column `min-h-screen flex items-center justify-center bg-gray-50`, `max-w-md w-full space-y-8 p-8` — no sidebar.

**Errors**: simple inline messages use `text-red-600` (see Campaigns error state).

## Components (prefer these)

| Use case | Component / pattern |
|----------|----------------------|
| Primary actions | `Button` from `components/ui/Button.jsx` — variants: `default`, `outline`, `secondary`, `destructive`, `ghost`, `link`; sizes `sm` / default / `lg` |
| Nav as button | `Button as={Link} to="…"` |
| Grouped content, tables, charts | `Card` from `components/ui/Card.jsx` — default `bg-white rounded-lg shadow p-4`; pass `className` for `overflow-auto`, extra padding |
| Labeled text fields | `Input` from `components/ui/Input.jsx` — includes label and error line styling |
| Dates | `DatePicker` from `components/ui/DatePicker.jsx` — trigger uses `border-gray-300`, `hover:border-teal-400` |

**Merging classes**: use `cn()` from `utils/cn.js` (`tailwind-merge` + `classnames`) when extending primitives.

Avoid introducing a second button or card style unless you extend the existing components.

## Color and semantic states

Patterns already in use:

- **Success / active**: green text or `bg-green-100 text-green-600` badges (campaign status, schedule badges)
- **Warning / paused**: `amber-600`, `bg-amber-400` progress bars
- **Info / completed**: `blue-600`, `bg-blue-500`
- **Draft / muted**: `text-gray-400`
- **Destructive**: `red-600`, `bg-red-100`, `Button` `destructive`
- **Teal accent**: primary actions, links, selected pills (`bg-teal-500 text-white`), schedule row hovers (`bg-teal-50` / `bg-teal-100`)

**Pill / toggle filters** (e.g. Analytics date presets): `rounded-full border text-xs font-medium`; selected `bg-teal-500 text-white border-teal-500`; unselected `bg-white text-gray-600 border-gray-300 hover:border-teal-300 hover:bg-teal-50`.

**Tag chips**: e.g. `bg-gray-200 rounded-full px-2 py-0.5 text-sm`.

## Typography

- Body: default sans (Figtree); **labels** often `text-sm font-medium text-gray-700`
- **Secondary / help text**: `text-sm` or `text-xs` + `text-gray-500` / `text-gray-400`
- **Mono data** (IDs, times): `font-mono text-xs` where tables or schedule rows need scanability
- **Uppercase section labels** (e.g. Settings TOC): `text-xs uppercase tracking-wider text-gray-400 font-semibold`

## Forms

- Native inputs get global styles from `index.css` (`rounded-md`, `border-gray-300`, `shadow-sm`, focus ring toward teal)
- Custom selects in complex forms often add `rounded-lg`, `focus:ring-2 focus:ring-teal-300` — stay consistent with Settings / webhooks UI
- Checkboxes: `className="rounded"` is the common pattern

## Tables

- Global table rules in `index.css`: bordered cells, zebra `even:bg-gray-50`
- **Inside cards**: wrap with `Card className="overflow-auto"` and `table className="w-full table-auto border-collapse"` when horizontal scroll is possible
- **Drag reorder** (priority campaigns): use existing `tr.dragging` / `tr.drag-over` rules in `index.css`

## Links

- Global `a` styles: `text-teal-500 hover:underline`
- In dark sidebar / chrome: use `!no-underline` / `hover:no-underline` where nav should not look like prose links

## Feedback patterns

- **Toasts**: `useNotify()` from `NotificationContext` — success/error; keep messages short
- **Confirm dialogs**: `useConfirm()` from `ConfirmContext` — modal uses white card, `Button` outline + default
- **Full-screen loading**: `useLoading()` from `LoadingContext` — `.loader` spinner (defined in `index.css`)
- Do not add ad-hoc `alert()` for flows that should use these providers

## Motion

- Sidebar nav: `transition-colors`, `active:scale-95`, `hover:scale-102`, `duration-150`
- Onboarding / modals: `animate-in` keyframes in `index.css`
- Charts: optional `chart-reveal-up` class where appropriate

Keep motion subtle; avoid large layout shifts.

## Focus and accessibility

- Mouse focus outlines are suppressed globally; **keyboard** focus uses `focus-visible` with **teal** outline (`index.css` uses `theme('colors.teal.500')`)
- Recharts focus rings are explicitly removed — do not re-enable for chart SVGs
- Use `sr-only` labels where controls are visually obvious but need a name (see Analytics campaign select)

## Scrollbars

Slim scrollbars are themed in `index.css` for WebKit and Firefox, including `.dark` thumb colors. Avoid resetting scrollbar styles on new full-page containers unless necessary.

## Icons

- Prefer **Remix Icons** (`Ri*`) at **20px** in the sidebar; scale proportionally in page content
- Notification badges: small rounded full red pill with white text (see Unibox item in `Sidebar.jsx`)

## Charts

- **Recharts** is the standard; keep tick fonts small (`fontSize: 11`), use `CartesianGrid strokeDasharray="3 3"`, and ensure tooltips don’t sit under opaque layers (`wrapperStyle` / z-index as in Analytics)

## File placement

- **Routes / screens**: `src/pages/*.jsx`
- **Shared UI**: `src/components/ui/`
- **Layout-only / providers**: `src/components/` and `src/context/`
- **API**: `src/api.js` — prefer existing `api` helpers for consistency with caching and errors

## Checklist for a new page

1. Wrap content in `p-8 space-y-6` (or the Settings-style layout if it is a long settings-like document)
2. Single `h1` with established title classes
3. Use `Card` for grouped blocks; `Button` / `Input` / `DatePicker` instead of one-off styled elements
4. Use `primary` / teal and gray scale consistently; reuse badge and status colors from existing pages
5. Wire destructive or irreversible actions through `useConfirm` and outcomes through `useNotify`
6. Respect layout margin for the fixed sidebar
7. If surfaces must work in dark mode, add `dark:` classes alongside defaults where Dark Reader alone is insufficient

## Intentional exceptions

- **OAuth buttons on Login** use neutral gray borders and **blue** `focus:ring-blue-500` to match common OAuth button patterns — not the app primary teal
- **Confirm modal** uses an explicit white background and `data-darkreader-ignore` so the dialog stays readable when Dark Reader is on

When in doubt, open **Analytics**, **Campaigns**, or **Settings** and mirror their structure and class choices.
