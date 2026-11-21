import { useEffect, useState } from "react";
import { MessageSquare } from "lucide-react";
import { api } from "../../api/client";
import { AcToaster, ErrorBanner, PageHeader, Skeleton, errMsg } from "./AcCommon";

const FILTERS = [["all", "All"], ["voice", "Calls"], ["chat", "Chats"]];

export default function AcConversations() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(null);

  const load = () => {
    setError("");
    api.get("/ai-calling/conversations", { skipLoader: true }).then(({ data }) => setRows(data)).catch((e) => { setError(errMsg(e)); setRows((c) => c ?? []); });
  };
  useEffect(load, []);
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && setOpen(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const shown = (rows || []).filter((c) => filter === "all" || c.channel === filter);

  return (
    <div className="ac-page">
      <PageHeader eyebrow="AI Calling · Inbound" title="Conversations" subtitle="Calls and chats, and how each one was routed. Sample data for now." />
      <ErrorBanner message={error} onRetry={load} />
      <div className="panel">
        {rows === null ? (
          <Skeleton rows={4} />
        ) : (
          <>
            <div className="ac-toolbar">
              <div className="ac-seg" role="group" aria-label="Filter by channel">
                {FILTERS.map(([v, l]) => <button key={v} type="button" aria-pressed={filter === v} onClick={() => setFilter(v)}>{l}</button>)}
              </div>
              <span className="ac-small ac-muted">{shown.length} shown</span>
            </div>
            {shown.length === 0 ? (
              <div className="ac-empty"><MessageSquare size={32} color="var(--green)" /><h3>No conversations</h3><p>They will appear here once callers start using the assistant.</p></div>
            ) : (
              <div className="ac-table-wrap">
                <table className="data-table">
                  <thead><tr><th>Customer</th><th>Channel</th><th>Started</th><th>Routed to</th><th>Still queued</th><th></th></tr></thead>
                  <tbody>
                    {shown.map((c) => (
                      <tr key={c.id}>
                        <td>{c.customer}</td>
                        <td><span className="badge badge-gray">{c.channel}</span></td>
                        <td className="ac-small">{new Date(c.startedAt).toLocaleString()}</td>
                        <td>{(c.routed || []).join(" → ") || "—"}</td>
                        <td>{(c.queue || []).join(", ") || "—"}</td>
                        <td><div className="ac-row-actions"><button type="button" className="btn-secondary" onClick={() => setOpen(c)}>View</button></div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
      {open && (
        <div className="modal-overlay" onClick={() => setOpen(null)}>
          <div className="modal-card" role="dialog" aria-modal="true" aria-label="Transcript" onClick={(e) => e.stopPropagation()}>
            <div className="modal-title">{open.customer} · {open.channel}</div>
            {(open.transcript || []).map((t, i) => (
              <p key={i}><strong>{t.role === "agent" ? "Assistant" : "Customer"}:</strong> {t.text}</p>
            ))}
            <div className="modal-actions"><button type="button" className="btn-secondary" onClick={() => setOpen(null)}>Close</button></div>
          </div>
        </div>
      )}
      <AcToaster />
    </div>
  );
}
