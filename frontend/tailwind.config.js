/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      screens: {
        // Below this the Review detail pane covers the list instead of sitting
        // beside it; the same number gates both halves of that switch.
        wide: '1101px',
      },
      colors: {
        // Nocturne — a dark, low-chroma interface with one accent used as a
        // line and a mark, never as a flood. There is no card fill: `bg` is the
        // only background, and elevation is an edge.
        bg: '#161826',
        text: '#e9e9ed',
        accent: {
          DEFAULT: '#9184d9',
          300: '#d2cefd',
          400: '#b5abfc',
          600: '#796cbf',
        },
        neutral: {
          500: '#9397ab',
          600: '#75798c',
          700: '#595d6c',
          900: '#292b31',
        },
        line: 'rgba(233,233,237,0.16)',
        'line-soft': 'rgba(233,233,237,0.10)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        // The design works in half-pixel steps; these are its exact sizes.
        '2xs': ['10.5px', '1.35'],
        '3xs': ['10px', '1.3'],
        xs: ['11px', '1.4'],
        'xs+': ['11.5px', '1.5'],
        sm: ['12px', '1.4'],
        'sm+': ['12.5px', '1.4'],
        base: ['13px', '1.5'],
        'base+': ['13.5px', '1.45'],
        md: ['14px', '1.4'],
        lg: ['15px', '1.3'],
        xl: ['20px', '1.2'],
        '2xl': ['22px', '1.2'],
        '3xl': ['26px', '1.15'],
        '4xl': ['30px', '1'],
      },
      spacing: {
        rail: '60px',
        sidebar: '212px',
        setupIndex: '196px',
        list: '452px',
      },
      maxWidth: {
        setup: '760px',
        blurb: '520px',
      },
      borderRadius: {
        DEFAULT: '8px',
        bar: '2px',
      },
      transitionTimingFunction: {
        // The one easing curve in the system.
        soft: 'cubic-bezier(.22,.61,.36,1)',
      },
      transitionDuration: {
        180: '180ms',
        240: '240ms',
        300: '300ms',
        340: '340ms',
        420: '420ms',
        550: '550ms',
        800: '800ms',
      },
      keyframes: {
        viewIn: {
          from: { opacity: '0', transform: 'translateY(7px)' },
          to: { opacity: '1', transform: 'none' },
        },
        panelIn: {
          from: { opacity: '0', transform: 'translateX(12px)' },
          to: { opacity: '1', transform: 'none' },
        },
        cardIn: {
          from: { opacity: '0', transform: 'translateY(10px)' },
          to: { opacity: '1', transform: 'none' },
        },
        dotPulse: {
          '0%,100%': { boxShadow: '0 0 0 3px rgba(145,132,217,.18)' },
          '50%': { boxShadow: '0 0 0 8px rgba(145,132,217,.05)' },
        },
        sweep: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(320%)' },
        },
        spin360: { to: { transform: 'rotate(360deg)' } },
      },
      animation: {
        viewIn: 'viewIn 300ms cubic-bezier(.22,.61,.36,1) both',
        panelIn: 'panelIn 300ms cubic-bezier(.22,.61,.36,1) both',
        tabIn: 'viewIn 240ms ease both',
        cardIn: 'cardIn 340ms cubic-bezier(.22,.61,.36,1) both',
        rowIn: 'cardIn 420ms cubic-bezier(.22,.61,.36,1) both',
        dotPulse: 'dotPulse 1.2s ease-in-out infinite',
        sweep: 'sweep 1.4s linear infinite',
        spin360: 'spin360 900ms linear infinite',
      },
    },
  },
  plugins: [],
}
