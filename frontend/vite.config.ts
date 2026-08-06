import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", ["BACKEND_PORT", "FRONTEND_PORT"]);
  const backendPort = env.BACKEND_PORT || "8000";
  const frontendPort = Number(env.FRONTEND_PORT || "5173");

  return {
    plugins: [react()],
    server: {
      port: frontendPort,
      strictPort: true,
      proxy: {
        "/api": `http://127.0.0.1:${backendPort}`,
      },
    },
  };
});

