import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { AcToaster, ErrorBanner, Field, PageHeader, Skeleton, errMsg, toast, Card } from "./AcCommon";
import { Route } from "lucide-react";

const PROB = [
  ["T_high", "Confidence to route directly", "The top use case must score at least this to be chosen on its own.", 0.05],
  ["margin", "Lead over the runner-up", "How far ahead the top use case must be.", 0.05],
  ["T_multi", "Confidence to add a second request", "Each extra use case needs at least this score to be queued.", 0.05],
];
const INTS = [
  ["maxIntentsPerCall", "Most requests per call", "Cap on queued use cases.", 1, 5],
  ["maxRoutingTurns", "Most clarifying questions", "After this, the default use case is used.", 1, 5],
];

function validate(s) {
  const e = {};
  if (!s.purposeGreeting.trim()) e.purposeGreeting = "The opening cannot be empty.";
  if (!(s.companyName || "").trim()) e.companyName = "Enter the business name.";
  PROB.forEach(([k]) => { if (!(s[k] >= 0 && s[k] <= 1)) e[k] = "Enter a number between 0 and 1."; });
  INTS.forEach(([k, , , min, max]) => { if (!Number.isInteger(s[k]) || s[k] < min || s[k] > max) e[k] = `Enter a whole number from ${min} to ${max}.`; });
  return e;
}

export default function AcRouting() {
  const [s, setS] = useState(null);
  const [saved, setSaved] = useState("");
  const [ucs, setUcs] = useState([]);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [showErrors, setShowErrors] = useState(false);

  const load = () => {
    setError("");
    Promise.all([api.get("/ai-calling/settings", { skipLoader: true }), api.get("/ai-calling/usecases", { skipLoader: true })])
      .then(([a, b]) => { setS(a.data); setSaved(JSON.stringify(a.data)); setUcs(b.data); })
      .catch((e) => setError(errMsg(e)));
  };
  useEffect(load, []);

  const errors = s ? validate(s) : {};
  const dirty = s && JSON.stringify(s) !== saved;
  const err = (k) => (showErrors ? errors[k] : undefined);

  async function save(e) {
    e.preventDefault();
    setShowErrors(true);
    if (Object.keys(errors).length) return toast("Fix the highlighted fields first.", "error");
    setSaving(true);
    try {
      const { data } = await api.put("/ai-calling/settings", s, { skipLoader: true });
      setS(data); setSaved(JSON.stringify(data)); setShowErrors(false);
      toast("Routing settings saved");
    } catch (er) {
      toast(errMsg(er), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="ac-page narrow">
      <PageHeader eyebrow="AI Calling · Inbound" title="Routing" subtitle="How the assistant opens the call, asks what the caller needs, and picks use cases." />
      <ErrorBanner message={error} onRetry={load} />
      {!s ? (
        !error && <div className="ac-card"><Skeleton rows={5} /></div>
      ) : (
        <form onSubmit={save} noValidate>
          <Card icon={Route} title="How the assistant answers" desc="It picks up like a person: a short hello, then listens. When the caller says what they need, it works out the right use case and follows that use case's prompt.">
            <Field label="Business name" htmlFor="rt-company" required error={err("companyName")}
              hint="Who the assistant answers for. If a caller asks “is this Rekon Sales?”, it confirms in a word and asks how it can help.">
              <input id="rt-company" className={`field-input ${showErrors && errors.companyName ? "is-invalid" : ""}`} value={s.companyName || ""} maxLength={60}
                onChange={(e) => setS({ ...s, companyName: e.target.value })} />
            </Field>
            <Field label="First words when the call connects" htmlFor="rt-open" required error={err("purposeGreeting")}
              hint="Keep it short, like “हेलो।”. Optional: {options} becomes the top three use cases, if you do want it to list them.">
              <input id="rt-open" className={`field-input ${showErrors && errors.purposeGreeting ? "is-invalid" : ""}`} value={s.purposeGreeting}
                onChange={(e) => setS({ ...s, purposeGreeting: e.target.value })} />
            </Field>
            <Field label="If the request is still unclear" htmlFor="rt-default" error={err("defaultUsecaseId")}
              hint="After the clarifying questions. Requests that are clearly outside your use cases are always declined politely, and the assistant says what it can help with.">
              <select id="rt-default" className="select-input" value={s.defaultUsecaseId || ""} onChange={(e) => setS({ ...s, defaultUsecaseId: e.target.value })}>
                <option value="">Say what it can help with and ask again</option>
                {ucs.map((u) => <option key={u.usecaseId} value={u.usecaseId}>{u.name}</option>)}
              </select>
            </Field>
          </Card>

          <Card title="Matching sensitivity" desc="Leave these alone unless the assistant picks the wrong use case. Values are probabilities from 0 to 1.">
            <div className="ac-grid-2">
              {PROB.map(([k, label, hint, step]) => (
                <Field key={k} label={label} htmlFor={`rt-${k}`} hint={hint} error={err(k)}>
                  <input id={`rt-${k}`} className={`field-input ${showErrors && errors[k] ? "is-invalid" : ""}`} type="number" min={0} max={1} step={step} value={s[k]}
                    onChange={(e) => setS({ ...s, [k]: e.target.value === "" ? "" : Number(e.target.value) })} />
                </Field>
              ))}
              {INTS.map(([k, label, hint, min, max]) => (
                <Field key={k} label={label} htmlFor={`rt-${k}`} hint={hint} error={err(k)}>
                  <input id={`rt-${k}`} className={`field-input ${showErrors && errors[k] ? "is-invalid" : ""}`} type="number" min={min} max={max} step={1} value={s[k]}
                    onChange={(e) => setS({ ...s, [k]: e.target.value === "" ? "" : Number(e.target.value) })} />
                </Field>
              ))}
            </div>
          </Card>

          <div className="ac-savebar">
            <span className={dirty ? "ac-dirty" : "ac-clean"}>{dirty ? "Unsaved changes" : "All changes saved"}</span>
            <div className="ac-actions">
              <button type="button" className="btn-secondary" disabled={!dirty || saving} onClick={() => { setS(JSON.parse(saved)); setShowErrors(false); }}>Reset</button>
              <button type="submit" className="btn-primary" disabled={!dirty || saving}>{saving ? "Saving…" : "Save changes"}</button>
            </div>
          </div>
        </form>
      )}
      <AcToaster />
    </div>
  );
}
