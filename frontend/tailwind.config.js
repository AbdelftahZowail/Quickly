module.exports = {
  content: [
    './index.html',
    './src/**/*.{js,jsx,ts,tsx}'
  ],
  theme: {
    extend: {
      colors: {
        // primary color used throughout the app; can be changed globally
        primary: '#14b8a6',
      },
      fontFamily: {
        sans: ['Figtree', 'ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont',
          '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', '"Noto Sans"', 'sans-serif'],
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'), // better base styling for form controls
  ]
};