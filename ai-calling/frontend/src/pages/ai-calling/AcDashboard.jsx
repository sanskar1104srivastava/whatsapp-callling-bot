import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { AcToaster, ErrorBanner, PageHeader, Skeleton, errMsg } from "./AcCommon";
import { P } from "./acPaths";

export default function AcDashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = () => {
    setError("");
    Promise.all([api.get("/ai-calling/usecases", { skipLoader: true }), api.get("/ai-calling/conversations", { skipLoader: true })])
      .then(([u, c]) => setData({ ucs: u.data, convs: c.data }))
      .catch((e) => { setError(errMsg(e)); setData((cur) => cur ?? { ucs: [], convs: [] }); });
  };
  useEffect(load, []);

  const { ucs = [], convs = [] } = data || {};
  const n = (fn) => convs.filter(fn).length;
  const cards = [
    ["Active use cases", ucs.filter((u) => u.enabled).length, `of ${ucs.length} configured`],
    ["Inbound calls", n((c) => c.channel === "voice"), "WhatsApp voice"],
    ["Inbound chats", n((c) => c.channel === "chat"), "WhatsApp messages"],
    ["Multi-request", n((c) => (c.routed || []).length > 1), "needed more than one use case"],
  ];
  const byUc = ucs.map((u) => ({ name: u.name, n: n((c) => (c.routed || []).includes(u.usecaseId)) })).sort((a, b) => b.n - a.n);

  return (
    <div className="ac-page">
      <PageHeader title="Dashboard" subtitle="A quick look at your inbound assistant. Figures are sample data until live calls are connected." />
      <ErrorBanner message={error} onRetry={load} />
      {!data ? (
        <div className="panel"><Skeleton rows={4} /></div>
      ) : (
        <>
          <div className="stat-grid">
            {cards.map(([label, value, cap]) => (
              <div className="stat-card" key={label}>
                <div className="stat-card-head"><span className="stat-card-label">{label}</span></div>
                <div className="stat-card-value">{value}</div>
                <div className="stat-card-subcaption">{cap}</div>
              </div>
            ))}
          </div>

          <div className="panels-row" style={{ marginTop: "1rem" }}>
            <div className="panel">
              <div className="panel-title">Requests by use case</div>
              {byUc.length === 0 ? (
                <div className="ac-empty"><p>No use cases yet.</p><Link className="btn-primary" to={P.usecaseNew}>Add use case</Link></div>
              ) : (
                <table className="data-table"><tbody>
                  {byUc.map((r) => <tr key={r.name}><td>{r.name}</td><td style={{ textAlign: "right" }}><strong>{r.n}</strong></td></tr>)}
                </tbody></table>
              )}
            </div>
            <div className="panel">
              <div className="panel-title">Recent conversations</div>
              {convs.length === 0 ? (
                <div className="ac-empty"><p>Nothing yet.</p></div>
              ) : (
                <table className="data-table"><tbody>
                  {convs.slice(0, 5).map((c) => (
                    <tr key={c.id}>
                      <td>{c.customer}</td>
                      <td><span className="badge badge-gray">{c.channel}</span></td>
                      <td className="ac-small ac-muted">{(c.routed || []).join(" → ") || "—"}</td>
                    </tr>
                  ))}
                </tbody></table>
              )}
              <p className="ac-small" style={{ marginBottom: 0 }}><Link className="ac-link" to={P.conversations}>View all conversations</Link></p>
            </div>
          </div>

          <div className="panel" style={{ marginTop: "1rem" }}>
            <div className="panel-title">Quick actions</div>
            <div className="ac-quick">
              <Link to={P.usecaseNew}><strong>Add a use case</strong><span className="ac-small ac-muted">Teach the assistant a new kind of request.</span></Link>
              <Link to={P.usecases}><strong>Set priorities</strong><span className="ac-small ac-muted">Decide which request is handled first.</span></Link>
              <Link to={P.routing}><strong>Edit the opening</strong><span className="ac-small ac-muted">Change how the assistant greets callers.</span></Link>
            </div>
          </div>
        </>
      )}
      <AcToaster />
    </div>
  );
}
