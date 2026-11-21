import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Menu } from "lucide-react";
import { AI_CALLING_GROUPS } from "../pages/ai-calling";
import "./dev.css";

function NavGroup({ title, links, onNavigate }) {
  return (
    <div>
      <span className="sidebar-nav-label">{title}</span>
      <nav className="sidebar-nav" style={{ marginBottom: "1.1rem" }}>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} onClick={onNavigate} className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`}>
            <l.icon size={18} strokeWidth={2} />
            {l.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

export default function Layout() {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);
  return (
    <div className="app-shell">
      <div className="dev-topbar">
        <button type="button" className="dev-menu-btn" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)}><Menu size={20} /></button>
        <strong>Vriddhi AI</strong>
      </div>
      {open && <div className="dev-scrim" onClick={() => setOpen(false)} />}
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="sidebar-brand">
          <img className="sidebar-logo" src="/brand/vriddhi-icon.png" alt="Vriddhi AI" />
          <div>
            <div style={{ fontFamily: "var(--font-display)", fontWeight: 700, fontSize: "1.02rem" }}>Vriddhi AI</div>
            <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>AI Calling</div>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto" }}>
          {AI_CALLING_GROUPS.map((g) => <NavGroup key={g.title} {...g} onNavigate={() => setOpen(false)} />)}
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
