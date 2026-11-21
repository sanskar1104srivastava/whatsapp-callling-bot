// Shared bits for the AI Calling pages: toasts, confirm dialog, loading + error states, error text.
import { useEffect, useState } from "react";
import "./ac.css";

/** Readable message for any axios/FastAPI error (422 detail arrays, network down, 404 ...). */
export function errMsg(err) {
  if (!err) return "Something went wrong";
  if (err.code === "ERR_NETWORK" || (!err.response && err.request)) {
    return "Cannot reach the AI Calling API. Is the backend running?";
  }
  const d = err.response?.data?.detail;
  if (Array.isArray(d)) {
    return d
      .map((x) => `${(x.loc || []).filter((p) => p !== "body").join(".") || "request"}: ${String(x.msg || "").replace(/^Value error, /, "")}`)
      .join("; ");
  }
  return d || err.message || "Something went wrong";
}

// --- toasts: call toast("Saved") from anywhere, render <AcToaster /> once per page ---
const listeners = new Set();
let nextId = 1;
export function toast(message, type = "success") {
  const t = { id: nextId++, message, type };
  listeners.forEach((fn) => fn(t));
}
export function AcToaster() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const fn = (t) => {
      setItems((cur) => [...cur, t]);
      setTimeout(() => setItems((cur) => cur.filter((x) => x.id !== t.id)), t.type === "error" ? 6000 : 3000);
    };
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  return (
    <div className="ac-toaster" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`ac-toast ${t.type}`}>{t.message}</div>
      ))}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div className="ac-banner" role="alert">
      <span>{message}</span>
      {onRetry && <button type="button" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Skeleton({ rows = 4 }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="ac-skel" style={{ height: 22, margin: "0.9rem 0", width: `${90 - i * 9}%` }} />
      ))}
    </div>
  );
}

export function ConfirmDialog({ title, body, confirmLabel = "Delete", busy, onConfirm, onCancel }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);
  return (
    <div className="modal-overlay" onClick={() => !busy && onCancel()}>
      <div className="modal-card" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">{title}</div>
        <p className="ac-muted" style={{ marginTop: 0 }}>{body}</p>
        <div className="modal-actions">
          <button type="button" className="btn-secondary" disabled={busy} onClick={onCancel}>Cancel</button>
          <button type="button" className="btn-primary" style={{ width: "auto", background: "#b91c1c" }} disabled={busy} onClick={onConfirm}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PageHeader({ eyebrow = "AI Calling", title, subtitle, children }) {
  return (
    <div className="page-header">
      <div>
        <p className="page-eyebrow">{eyebrow}</p>
        <h1 className="page-title">{title}</h1>
        {subtitle && <div className="page-subtitle">{subtitle}</div>}
      </div>
      {children}
    </div>
  );
}

/** Label + control + hint/error. Pass the control as children; it gets id via `htmlFor`. */
export function Field({ label, htmlFor, required, hint, error, counter, children }) {
  return (
    <div className="ac-field">
      <label htmlFor={htmlFor}>
        {label}
        {required && <span className="ac-req" aria-hidden="true">*</span>}
        {counter}
      </label>
      {children}
      {error ? <div className="ac-err" role="alert">{error}</div> : hint && <div className="ac-hint">{hint}</div>}
    </div>
  );
}

export function Card({ icon: Icon, title, desc, children }) {
  return (
    <section className="ac-card">
      <div className="ac-card-head">
        {Icon && <span className="ac-card-icon"><Icon size={18} /></span>}
        <div>
          <h2 className="ac-card-title">{title}</h2>
          {desc && <p className="ac-card-desc">{desc}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}
