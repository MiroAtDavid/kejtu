import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [sveltekit()],
  // Lazy chart imports must be prepared at startup: discovering them mid-reply
  // makes the development desktop reload its page.
  optimizeDeps: { include: ["layerchart/svg", "d3-scale"] },
  // The daily development app changes only through an idle-checked restart.
  server: {
    // Only the public manual is shared with Python. Never expose the repository
    // root (and its private .state) through the development file server.
    fs: {
      allow: [
        fileURLToPath(new URL(".", import.meta.url)),
        fileURLToPath(new URL("../src/kejtu_next/help", import.meta.url)),
      ],
    },
    host: "127.0.0.1",
    port: 1431,
    strictPort: true,
    hmr: false,
    watch: { ignored: ["**/*"] },
  },
});
