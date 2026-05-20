/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0F1117',
        card: '#1A1D2E',
        accent: '#4FC3F7',
        success: '#4CAF50',
        warning: '#FFC107',
        danger: '#FF5370',
        textPrimary: '#E0E0E0',
        textSecondary: '#9E9E9E',
        border: '#2A2D3E',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
