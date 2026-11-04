/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { 50: '#eefbf4', 100: '#d6f5e3', 500: '#16a34a', 600: '#15803d', 700: '#166534' },
      },
      fontFamily: { sans: ['-apple-system', 'BlinkMacSystemFont', '"PingFang SC"', '"Segoe UI"', 'Roboto', 'sans-serif'] },
    },
  },
  plugins: [],
}
