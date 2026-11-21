// Local shell only, not copied into Rekon-AI. There, add acRoutes to firm-portal's App.jsx and AI_CALLING_GROUPS to Layout.jsx.
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./Layout";
import { acRoutes } from "../pages/ai-calling";
import { P } from "../pages/ai-calling/acPaths";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          {acRoutes.map((r) => (
            <Route key={r.path} path={r.path} element={r.element} />
          ))}
        </Route>
        <Route path="*" element={<Navigate to={P.home} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
