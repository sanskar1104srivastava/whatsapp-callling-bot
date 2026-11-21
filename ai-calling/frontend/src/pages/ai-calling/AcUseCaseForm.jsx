import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AudioLines, Bot, MessageSquareQuote, Route, SlidersHorizontal, Volume2, X } from "lucide-react";
import { api } from "../../api/client";
import { Card, ErrorBanner, Field, PageHeader, Skeleton, AcToaster, errMsg, toast } from "./AcCommon";
import AcToggle from "./AcToggle";
import { P } from "./acPaths";

const EMPTY = {
  usecaseId: "", name: "", spokenName: "", type: "custom", intentDescription: "", examples: [], prompt: "",
  greeting: "", closing: "", language: "hi", gender: "female", voice: "", backgroundSound: "", toolSound: "",
  knowledgeBaseId: "", slots: [], tools: [], channels: ["chat", "voice"], priority: 99, canPreempt: false, enabled: true,
};
const TYPES = [["order", "Order"], ["appointment", "Appointment"], ["faq", "FAQ / information"], ["complaint", "Complaint"], ["custom", "Custom"]];
// LiveKit built-in clips; same list as the AlgoTalks context form.
const SOUNDS = [
  ["", "None"], ["office_ambience", "Office"], ["city_ambience", "City"], ["crowded_room", "Crowded room"],
  ["forest_ambience", "Forest"], ["keyboard_typing", "Keyboard typing"], ["keyboard_typing2", "Keyboard typing 2"], ["hold_music", "Hold music"],
];
const ID_RE = /^[a-z0-9_-]{2,40}$/;
const snap = (f, ex) => JSON.stringify(f) + "|" + ex; // for the unsaved-changes check
const lines = (s) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
const langOk = (v, lang) => (lang === "hi" ? v.language === "hi" || v.language === "enhi" : v.language === "en");
const voiceLabel = (v) => `${v.persona} · ${v.style}${v.language === "enhi" ? " · Hinglish" : ""}`;

function validate(f, examplesText, isEdit) {
  const e = {};
  if (!f.name.trim()) e.name = "Give the use case a name.";
  else if (f.name.length > 80) e.name = "Keep the name under 80 characters.";
  if (!isEdit && !ID_RE.test(f.usecaseId)) e.usecaseId = "2-40 characters: lowercase letters, digits, - or _.";
  if (f.intentDescription.trim().length < 10) e.intentDescription = "Describe in a sentence when a caller needs this (at least 10 characters).";
  if (f.prompt.trim().length < 20) e.prompt = "Write the instructions for the assistant (at least 20 characters).";
  if (f.greeting.length > 300) e.greeting = "Keep the greeting under 300 characters.";
  if (f.closing.length > 300) e.closing = "Keep the closing under 300 characters.";
  if (f.channels.length === 0) e.channels = "Pick at least one channel.";
  if (lines(examplesText).length > 20) e.examples = "At most 20 examples.";
  return e;
}

export default function AcUseCaseForm() {
  const { id } = useParams();
  const isEdit = !!id;
  const navigate = useNavigate();

  const [f, setF] = useState(EMPTY);
  const [examplesText, setExamplesText] = useState("");
  const [initial, setInitial] = useState(snap(EMPTY, ""));
  const [slotInput, setSlotInput] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [voices, setVoices] = useState([]);
  const [voicesErr, setVoicesErr] = useState("");
  const [loading, setLoading] = useState(isEdit);
  const [notFound, setNotFound] = useState(false);
  const [loadErr, setLoadErr] = useState("");
  const [showErrors, setShowErrors] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");
  const set = (k, v) => setF((p) => ({ ...p, [k]: v }));

  function loadVoices() {
    setVoicesErr("");
    api.get("/ai-calling/voices", { skipLoader: true }).then(({ data }) => setVoices(data)).catch((e) => setVoicesErr(errMsg(e)));
  }
  function loadUseCase() {
    if (!isEdit) return;
    setLoading(true); setLoadErr(""); setNotFound(false);
    api.get(`/ai-calling/usecases/${id}`)
      .then(({ data }) => {
        const merged = { ...EMPTY, ...data }; // older records miss the newer fields
        setF(merged);
        setExamplesText((merged.examples || []).join("\n"));
        setInitial(snap(merged, (merged.examples || []).join("\n")));
      })
      .catch((e) => (e.response?.status === 404 ? setNotFound(true) : setLoadErr(errMsg(e))))
      .finally(() => setLoading(false));
  }
  useEffect(loadVoices, []);
  useEffect(loadUseCase, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = snap(f, examplesText) !== initial;
  useEffect(() => {
    const warn = (e) => { if (dirty && !saving) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, saving]);

  const errors = useMemo(() => validate(f, examplesText, isEdit), [f, examplesText, isEdit]);

  // --- voice picker: filtered by language and gender
  const byLang = useMemo(() => voices.filter((v) => langOk(v, f.language)), [voices, f.language]);
  const groups = useMemo(() => {
    const matching = byLang.filter((v) => v.gender === f.gender);
    return {
      recommended: matching.filter((v) => v.recommended),
      rest: matching.filter((v) => !v.recommended).sort((a, b) => a.persona.localeCompare(b.persona) || a.style.localeCompare(b.style)),
      unknown: byLang.filter((v) => v.gender === "unknown").sort((a, b) => a.persona.localeCompare(b.persona) || a.style.localeCompare(b.style)),
    };
  }, [byLang, f.gender]);
  const selectedVoice = voices.find((v) => v.id === f.voice);

  function setLanguage(language) {
    setF((p) => {
      const cur = voices.find((v) => v.id === p.voice);
      return { ...p, language, voice: cur && !langOk(cur, language) ? "" : p.voice };
    });
  }
  function setGender(gender) {
    setF((p) => {
      const cur = voices.find((v) => v.id === p.voice);
      return { ...p, gender, voice: cur && cur.gender !== "unknown" && cur.gender !== gender ? "" : p.voice };
    });
  }
  function onNameChange(name) {
    setF((p) => ({ ...p, name, usecaseId: !isEdit && !idTouched ? slug(name) : p.usecaseId }));
  }
  function addSlot() {
    const v = slotInput.trim();
    if (!v) return;
    if (f.slots.some((s) => s.toLowerCase() === v.toLowerCase())) return toast("That detail is already listed.", "error");
    if (f.slots.length >= 12) return toast("At most 12 details per use case.", "error");
    set("slots", [...f.slots, v]);
    setSlotInput("");
  }
  const toggleChannel = (c) => set("channels", f.channels.includes(c) ? f.channels.filter((x) => x !== c) : [...f.channels, c]);

  async function save(e) {
    e.preventDefault();
    setShowErrors(true);
    if (Object.keys(errors).length) {
      toast("Fix the highlighted fields first.", "error");
      document.querySelector(".is-invalid")?.focus();
      return;
    }
    setSaving(true); setSaveErr("");
    const body = {
      ...f,
      name: f.name.trim(), spokenName: f.spokenName.trim(), intentDescription: f.intentDescription.trim(),
      prompt: f.prompt.trim(), greeting: f.greeting.trim(), closing: f.closing.trim(), examples: lines(examplesText),
    };
    try {
      await (isEdit ? api.put(`/ai-calling/usecases/${id}`, body) : api.post("/ai-calling/usecases", body));
      setInitial(snap(f, examplesText)); // not dirty any more, so the leave-warning stays quiet
      toast(isEdit ? "Use case updated" : "Use case created");
      navigate(P.usecases);
    } catch (err) {
      const msg = err.response?.status === 409 ? "A use case with this ID already exists. Pick another ID." : errMsg(err);
      setSaveErr(msg);
      toast(msg, "error");
    } finally {
      setSaving(false);
    }
  }
  function cancel() {
    if (dirty && !window.confirm("Discard your changes?")) return;
    navigate(P.usecases);
  }

  const inv = (k) => (showErrors && errors[k] ? "is-invalid" : "");
  const err = (k) => (showErrors ? errors[k] : undefined);

  if (notFound) {
    return (
      <div className="ac-page narrow">
        <PageHeader title="Use case not found" />
        <div className="ac-card ac-empty">
          <p>No use case with the ID <strong>{id}</strong>. It may have been deleted.</p>
          <Link className="btn-primary" to={P.usecases}>Back to use cases</Link>
        </div>
        <AcToaster />
      </div>
    );
  }

  return (
    <div className="ac-page narrow">
      <PageHeader
        eyebrow="AI Calling · Inbound"
        title={isEdit ? `Edit use case` : "Add use case"}
        subtitle="Configure what the assistant does for one kind of request: how it is recognised, what it says, and how it sounds."
      />
      <ErrorBanner message={loadErr} onRetry={loadUseCase} />
      <ErrorBanner message={saveErr} />
      {loading ? (
        <div className="ac-card"><Skeleton rows={6} /></div>
      ) : (
        <form onSubmit={save} noValidate>
          <Card icon={SlidersHorizontal} title="Basics" desc="Name it for your team. The spoken name is what the assistant says in its opening, so keep it short.">
            <div className="ac-grid-2">
              <Field label="Name" htmlFor="uc-name" required error={err("name")}>
                <input id="uc-name" className={`field-input ${inv("name")}`} value={f.name} maxLength={80} autoFocus={!isEdit}
                  onChange={(e) => onNameChange(e.target.value)} />
              </Field>
              <Field label="Name in the opening speech" htmlFor="uc-spoken" hint="Example: ऑर्डर, अपॉइंटमेंट, शिकायत">
                <input id="uc-spoken" className="field-input" value={f.spokenName} maxLength={40} onChange={(e) => set("spokenName", e.target.value)} />
              </Field>
              <Field label="ID" htmlFor="uc-id" required error={err("usecaseId")} hint={isEdit ? "The ID cannot be changed." : "Lowercase letters, digits, - or _. Filled from the name."}>
                <input id="uc-id" className={`field-input ${inv("usecaseId")}`} value={f.usecaseId} disabled={isEdit}
                  onChange={(e) => { setIdTouched(true); set("usecaseId", e.target.value.toLowerCase()); }} />
              </Field>
              <Field label="Type" htmlFor="uc-type">
                <select id="uc-type" className="select-input" value={f.type} onChange={(e) => set("type", e.target.value)}>
                  {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
            </div>
            <div className="toggle-row">
              <div>
                <div className="toggle-row-label">Enabled</div>
                <div className="toggle-row-desc">Disabled use cases are never offered or routed to.</div>
              </div>
              <AcToggle checked={f.enabled} onChange={(v) => set("enabled", v)} />
            </div>
          </Card>

          <Card icon={Route} title="How it is recognised" desc="The routing model matches the caller's words against this description, so write it the way a customer would ask.">
            <Field label="When does a caller need this?" htmlFor="uc-intent" required error={err("intentDescription")}>
              <textarea id="uc-intent" rows={2} className={`field-textarea ${inv("intentDescription")}`} value={f.intentDescription}
                onChange={(e) => set("intentDescription", e.target.value)} placeholder="Customer wants to place an order for products" />
            </Field>
            <Field label="Example sentences" htmlFor="uc-examples" error={err("examples")} hint="One per line. Hindi and Hinglish both work. Two or three are enough.">
              <textarea id="uc-examples" rows={3} className="field-textarea" value={examplesText} onChange={(e) => setExamplesText(e.target.value)}
                placeholder={"mujhe 2 kilo chawal chahiye\nमुझे ऑर्डर देना है"} />
            </Field>
          </Card>

          <Card icon={Bot} title="Agent prompt" desc="What the assistant should do once the caller is here.">
            <Field label="Instructions" htmlFor="uc-prompt" required error={err("prompt")}
              counter={<span className="ac-counter">{f.prompt.length} characters</span>}>
              <textarea id="uc-prompt" rows={8} className={`field-textarea ${inv("prompt")}`} value={f.prompt} onChange={(e) => set("prompt", e.target.value)}
                placeholder="You take orders politely in Hindi. Confirm items and quantity, then confirm the order." />
            </Field>
            <div className="ac-field">
              <label htmlFor="uc-slot">Details to collect</label>
              <div className="ac-add-row">
                <input id="uc-slot" className="field-input" value={slotInput} placeholder="item, quantity, address…" maxLength={40}
                  onChange={(e) => setSlotInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSlot(); } }} />
                <button type="button" className="btn-secondary" style={{ width: "auto", margin: 0 }} onClick={addSlot}>Add</button>
              </div>
              {f.slots.length > 0 && (
                <div className="ac-chips">
                  {f.slots.map((s) => (
                    <span key={s} className="ac-chip">{s}
                      <button type="button" aria-label={`Remove ${s}`} onClick={() => set("slots", f.slots.filter((x) => x !== s))}><X size={13} /></button>
                    </span>
                  ))}
                </div>
              )}
              <div className="ac-hint">The assistant asks for these one at a time before it saves the request.</div>
            </div>
          </Card>

          <Card icon={Volume2} title="Voice" desc="Sarvam bulbul v4 flash. Sarvam does not publish a gender for v4 voices; it is taken from the persona name, and voices we cannot place are listed separately.">
            <div className="ac-grid-2">
              <Field label="Language" htmlFor="uc-lang">
                <select id="uc-lang" className="select-input" value={f.language} onChange={(e) => setLanguage(e.target.value)}>
                  <option value="hi">Hindi (includes Hinglish voices)</option>
                  <option value="en">English</option>
                </select>
              </Field>
              <div className="ac-field">
                <span className="ac-label" id="uc-gender-l">Gender</span>
                <div className="ac-seg" role="group" aria-labelledby="uc-gender-l">
                  <button type="button" aria-pressed={f.gender === "female"} onClick={() => setGender("female")}>Female</button>
                  <button type="button" aria-pressed={f.gender === "male"} onClick={() => setGender("male")}>Male</button>
                </div>
              </div>
            </div>
            <Field label="Voice" htmlFor="uc-voice" hint={voicesErr ? undefined : `${byLang.length} voices for this language.`} error={voicesErr}>
              <select id="uc-voice" className="select-input" value={f.voice} disabled={!!voicesErr || !voices.length} onChange={(e) => set("voice", e.target.value)}>
                <option value="">Default voice</option>
                {groups.recommended.length > 0 && (
                  <optgroup label="Recommended for customer calls">
                    {groups.recommended.map((v) => <option key={v.id} value={v.id}>{voiceLabel(v)}</option>)}
                  </optgroup>
                )}
                {groups.rest.length > 0 && (
                  <optgroup label={`${f.gender === "female" ? "Female" : "Male"} voices`}>
                    {groups.rest.map((v) => <option key={v.id} value={v.id}>{voiceLabel(v)}</option>)}
                  </optgroup>
                )}
                {groups.unknown.length > 0 && (
                  <optgroup label="Gender not published by Sarvam">
                    {groups.unknown.map((v) => <option key={v.id} value={v.id}>{voiceLabel(v)}</option>)}
                  </optgroup>
                )}
              </select>
              {voicesErr && <button type="button" className="btn-secondary" style={{ width: "auto", marginTop: "0.5rem" }} onClick={loadVoices}>Reload voices</button>}
            </Field>
            {selectedVoice && (
              <div className="ac-voice-summary" aria-live="polite">
                <AudioLines size={16} />
                <strong>{selectedVoice.persona}</strong>
                <span className="ac-muted">{selectedVoice.style}</span>
                <span className={`ac-pill ${selectedVoice.gender === "unknown" ? "amber" : "green"}`}>
                  {selectedVoice.gender === "unknown" ? "gender not published" : selectedVoice.gender}
                </span>
                {selectedVoice.recommended && <span className="ac-pill green">recommended</span>}
                <code className="ac-small ac-muted">{selectedVoice.id}</code>
              </div>
            )}
          </Card>

          <Card icon={MessageSquareQuote} title="Greeting and closing" desc="Optional. The call opening itself is set under Routing.">
            <Field label="Greeting when this use case starts" htmlFor="uc-greet" error={err("greeting")}
              counter={<span className="ac-counter">{f.greeting.length}/300</span>}>
              <input id="uc-greet" className={`field-input ${inv("greeting")}`} value={f.greeting} maxLength={320} onChange={(e) => set("greeting", e.target.value)}
                placeholder="ठीक है, मैं आपका ऑर्डर ले लेती हूँ।" />
            </Field>
            <Field label="Closing" htmlFor="uc-close" error={err("closing")} counter={<span className="ac-counter">{f.closing.length}/300</span>}
              hint="Said just before the call ends.">
              <input id="uc-close" className={`field-input ${inv("closing")}`} value={f.closing} maxLength={320} onChange={(e) => set("closing", e.target.value)}
                placeholder="धन्यवाद! आपका दिन शुभ हो।" />
            </Field>
          </Card>

          <Card icon={AudioLines} title="Sounds" desc="Built-in sounds that make a call feel natural.">
            <div className="ac-grid-2">
              <Field label="Background sound" htmlFor="uc-bg" hint="Plays quietly through the call.">
                <select id="uc-bg" className="select-input" value={f.backgroundSound} onChange={(e) => set("backgroundSound", e.target.value)}>
                  {SOUNDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
              <Field label="Tool sound" htmlFor="uc-tool" hint="Plays while the assistant is looking something up or saving.">
                <select id="uc-tool" className="select-input" value={f.toolSound} onChange={(e) => set("toolSound", e.target.value)}>
                  {SOUNDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </Field>
            </div>
          </Card>

          <Card icon={SlidersHorizontal} title="Channels and priority behaviour">
            <div className="ac-field">
              <span className="ac-label" id="uc-ch-l">Works on</span>
              <div className="ac-seg" role="group" aria-labelledby="uc-ch-l">
                <button type="button" aria-pressed={f.channels.includes("voice")} onClick={() => toggleChannel("voice")}>Voice calls</button>
                <button type="button" aria-pressed={f.channels.includes("chat")} onClick={() => toggleChannel("chat")}>WhatsApp chat</button>
              </div>
              {err("channels") && <div className="ac-err" role="alert">{err("channels")}</div>}
            </div>
            <div className="toggle-row">
              <div>
                <div className="toggle-row-label">Can interrupt other use cases</div>
                <div className="toggle-row-desc">If a caller asks for this in the middle of another task, it jumps the queue.</div>
              </div>
              <AcToggle checked={f.canPreempt} onChange={(v) => set("canPreempt", v)} />
            </div>
            <p className="ac-hint">Priority order is set by dragging on the use cases list.</p>
          </Card>

          <div className="ac-savebar">
            <span className={dirty ? "ac-dirty" : "ac-clean"}>{dirty ? "Unsaved changes" : isEdit ? "All changes saved" : "Fill in the details above"}</span>
            <div className="ac-actions">
              <button type="button" className="btn-secondary" onClick={cancel} disabled={saving}>Cancel</button>
              <button type="submit" className="btn-primary" disabled={saving || (isEdit && !dirty)}>{saving ? "Saving…" : isEdit ? "Save changes" : "Create use case"}</button>
            </div>
          </div>
        </form>
      )}
      <AcToaster />
    </div>
  );
}
