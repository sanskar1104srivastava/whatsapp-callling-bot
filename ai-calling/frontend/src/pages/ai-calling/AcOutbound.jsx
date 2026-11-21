import { PhoneOutgoing } from "lucide-react";
import { PageHeader } from "./AcCommon";

export default function AcOutbound() {
  return (
    <div className="ac-page narrow">
      <PageHeader eyebrow="AI Calling" title="Outbound calling" subtitle="Campaigns and calls your assistant places to customers." />
      <div className="ac-card ac-empty">
        <PhoneOutgoing size={36} color="var(--green)" />
        <h3>Coming soon</h3>
        <p>Outbound calling will be added after inbound is live. Nothing to configure here yet.</p>
      </div>
    </div>
  );
}
