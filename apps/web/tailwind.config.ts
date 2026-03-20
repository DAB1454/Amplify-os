import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        brand: {
          gold: "#c9a84c",
        },
        gray: {
          950: "#0a0a0f",
        },
      },
    },
  },
  plugins: [],
};

export default config;
