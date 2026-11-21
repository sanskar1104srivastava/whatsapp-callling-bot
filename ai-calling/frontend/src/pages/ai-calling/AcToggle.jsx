export default function AcToggle({ checked, onChange, disabled }) {
  return (
    <label className="toggle-switch">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-switch-track">
        <span className="toggle-switch-thumb" />
      </span>
    </label>
  );
}
