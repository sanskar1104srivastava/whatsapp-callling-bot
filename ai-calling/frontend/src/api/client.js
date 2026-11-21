// Dev-only client (no auth). Rekon-AI already has api/client.js with the same `api` export, so pages work unchanged after merge.
import axios from "axios";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE_URL || "http://localhost:8101" });
