import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy, arrayMove } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, ListChecks, Plus } from "lucide-react";
import { api } from "../../api/client";
import { AcToaster, ConfirmDialog, ErrorBanner, PageHeader, Skeleton, errMsg, toast } from "./AcCommon";
import AcToggle from "./AcToggle";
import { P } from "./acPaths";

function Row({ uc, rank, voiceName, dragDisabled, busy, onToggle, onEdit, onDelete }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: uc.usecaseId, disabled: dragDisabled });
  return (
    <tr ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition, background: isDragging ? "var(--green-light)" : undefined, opacity: uc.enabled ? 1 : 0.65 }}>
      <td style={{ width: 84, whiteSpace: "nowrap" }}>
        <span {...(dragDisabled ? {} : { ...attributes, ...listeners })} className={`ac-drag ${dragDisabled ? "disabled" : ""}`}
          aria-label={dragDisabled ? "Clear the search to reorder" : `Drag to reorder ${uc.name}`} title={dragDisabled ? "Clear the search to reorder" : "Drag to reorder"}>
          <GripVertical size={16} />
        </span>{" "}
        <strong>{rank}</strong>
      </td>
      <td style={{ minWidth: 220 }}>
        <strong>{uc.name}</strong>
        <div className="ac-small ac-muted ac-clamp">{uc.intentDescription}</div>
      </td>
      <td>{uc.type}</td>
      <td className="ac-small">{voiceName}</td>
      <td>{(uc.channels || []).map((c) => <span key={c} className="badge badge-gray" style={{ marginRight: 4 }}>{c}</span>)}</td>
      <td>{uc.canPreempt ? <span className="badge badge-amber">can interrupt</span> : <span className="ac-muted">—</span>}</td>
      <td><AcToggle checked={!!uc.enabled} disabled={busy} onChange={(v) => onToggle(uc, v)} /></td>
      <td>
        <div className="ac-row-actions">
          <button type="button" className="btn-secondary" onClick={() => onEdit(uc)}>Edit</button>
          <button type="button" className="btn-secondary" onClick={() => onDelete(uc)}>Delete</button>
        </div>
      </td>
    </tr>
  );
}

export default function AcUseCases() {
  const navigate = useNavigate();
  const [items, setItems] = useState(null); // null = loading
  const [voices, setVoices] = useState([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState("");
  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));

  const load = () => {
    setError("");
    api.get("/ai-calling/usecases", { skipLoader: true }).then(({ data }) => setItems(data)).catch((e) => { setError(errMsg(e)); setItems((cur) => cur ?? []); });
  };
  useEffect(() => {
    load();
    api.get("/ai-calling/voices", { skipLoader: true }).then(({ data }) => setVoices(data)).catch(() => {}); // only used for labels
  }, []);

  const voiceName = (uc) => (uc.voice ? voices.find((v) => v.id === uc.voice)?.persona || uc.voice : "Default");
  const filtering = query.trim() !== "";
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (items || []).filter((u) => !q || `${u.name} ${u.intentDescription} ${u.type}`.toLowerCase().includes(q));
  }, [items, query]);

  async function onDragEnd({ active, over }) {
    if (!over || active.id === over.id || filtering) return;
    const before = items;
    const ids = items.map((u) => u.usecaseId);
    const next = arrayMove(ids, ids.indexOf(active.id), ids.indexOf(over.id));
    setItems(next.map((id) => items.find((u) => u.usecaseId === id))); // optimistic
    try {
      const { data } = await api.put("/ai-calling/usecases-order", next, { skipLoader: true });
      setItems(data);
      toast("Priority updated");
    } catch (e) {
      setItems(before); // roll back
      toast(errMsg(e), "error");
    }
  }

  async function toggle(uc, enabled) {
    setBusyId(uc.usecaseId);
    const before = items;
    setItems(items.map((u) => (u.usecaseId === uc.usecaseId ? { ...u, enabled } : u)));
    try {
      await api.put(`/ai-calling/usecases/${uc.usecaseId}`, { ...uc, enabled }, { skipLoader: true });
      toast(`${uc.name} ${enabled ? "enabled" : "disabled"}`);
    } catch (e) {
      setItems(before);
      toast(errMsg(e), "error");
    } finally {
      setBusyId("");
    }
  }

  async function confirmDelete() {
    setDeleting(true);
    try {
      await api.delete(`/ai-calling/usecases/${toDelete.usecaseId}`, { skipLoader: true });
      toast(`Deleted ${toDelete.name}`);
      setToDelete(null);
      load();
    } catch (e) {
      toast(errMsg(e), "error"); // e.g. the default use case cannot be deleted
      setToDelete(null);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="ac-page">
      <PageHeader eyebrow="AI Calling · Inbound" title="Use cases"
        subtitle="What your assistant can handle. Drag to set priority: when a caller needs more than one thing, the top one is handled first.">
        <button type="button" className="btn-primary" style={{ width: "auto" }} onClick={() => navigate(P.usecaseNew)}>
          <Plus size={16} style={{ verticalAlign: "-3px", marginRight: 4 }} />Add use case
        </button>
      </PageHeader>

      <ErrorBanner message={error} onRetry={load} />

      <div className="panel">
        {items === null ? (
          <Skeleton rows={5} />
        ) : items.length === 0 && !error ? (
          <div className="ac-empty">
            <ListChecks size={34} color="var(--green)" />
            <h3>No use cases yet</h3>
            <p>Add the first thing your assistant can do, such as taking orders or booking appointments.</p>
            <button type="button" className="btn-primary" onClick={() => navigate(P.usecaseNew)}>Add use case</button>
          </div>
        ) : (
          <>
            <div className="ac-toolbar">
              <input className="field-input ac-search" type="search" placeholder="Search use cases" aria-label="Search use cases" value={query} onChange={(e) => setQuery(e.target.value)} />
              <span className="ac-small ac-muted">{filtering ? `${shown.length} of ${items.length}` : `${items.length} use case${items.length === 1 ? "" : "s"}`}</span>
            </div>
            {shown.length === 0 ? (
              <div className="ac-empty"><p>No use case matches “{query}”.</p></div>
            ) : (
              <div className="ac-table-wrap">
                <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
                  <table className="data-table">
                    <thead>
                      <tr><th>Priority</th><th>Use case</th><th>Type</th><th>Voice</th><th>Channels</th><th>Interrupt</th><th>Enabled</th><th></th></tr>
                    </thead>
                    <SortableContext items={shown.map((u) => u.usecaseId)} strategy={verticalListSortingStrategy}>
                      <tbody>
                        {shown.map((u) => (
                          <Row key={u.usecaseId} uc={u} rank={items.indexOf(u) + 1} voiceName={voiceName(u)} dragDisabled={filtering} busy={busyId === u.usecaseId}
                            onToggle={toggle} onDelete={setToDelete} onEdit={(x) => navigate(P.usecase(x.usecaseId))} />
                        ))}
                      </tbody>
                    </SortableContext>
                  </table>
                </DndContext>
              </div>
            )}
          </>
        )}
      </div>

      {toDelete && (
        <ConfirmDialog title={`Delete “${toDelete.name}”?`} body="Callers will no longer be routed to it. This cannot be undone."
          busy={deleting} onConfirm={confirmDelete} onCancel={() => setToDelete(null)} />
      )}
      <AcToaster />
    </div>
  );
}
