/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// `base: "./"` is not cosmetic: under Home Assistant's ingress, this app is served
// from a per-install path the Supervisor assigns at runtime (something like
// /api/hassio_ingress/<token>/), never known at build time. Relative asset paths
// mean the built index.html and its JS/CSS resolve correctly under whatever prefix
// the page happened to load under; absolute (/assets/...) paths would silently
// break under ingress while working fine in local dev, which is exactly the kind
// of thing worth getting right before there's real HAOS hardware to catch it on.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // Local dev only: `hia serve` isn't serving this app yet, so point API/WS
      // calls at it directly. In production this app IS served by hia serve, so
      // relative fetch()/WebSocket calls (see src/api.ts) need no proxy at all.
      "/api": {
        target: "http://localhost:8099",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
