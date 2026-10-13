import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => ({
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8010",
        changeOrigin: true,
        configure: (proxy) =>
          proxy.on("proxyReq", (request, incoming) => {
            // Translate only this local dev UI's origin; preserve foreign origins
            // so the API's cross-origin write protection can reject them.
            if (
              ["http://127.0.0.1:8080", "http://localhost:8080"].includes(
                incoming.headers.origin ?? "",
              )
            ) {
              request.setHeader("origin", "http://127.0.0.1:8010");
            }
          }),
      },
    },
    port: 8080,
  },
  plugins: [react(), mode === "development" && componentTagger()].filter(
    Boolean,
  ),
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
}));
