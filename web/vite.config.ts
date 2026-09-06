import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" supaya hasil build bisa di-hosting di path mana pun
// (Vercel root, Cloudflare Pages, atau subdirektori).
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    target: "es2020",
    sourcemap: false,
  },
});