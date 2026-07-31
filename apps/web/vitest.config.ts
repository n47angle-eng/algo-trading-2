import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    css: true,
    // Parallel load can push multi-step userEvent sketch flows past 5s default.
    testTimeout: 15_000,
    hookTimeout: 15_000,
  },
});
