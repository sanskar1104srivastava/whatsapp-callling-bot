// Merge entry. In Rekon-AI: spread acRoutes into <Routes> in App.jsx and map AI_CALLING_GROUPS to <NavGroup> in Layout.jsx.
import { createElement } from "react";
import { Activity, ListChecks, MessageSquareText, PhoneOutgoing, Route } from "lucide-react";
import AcDashboard from "./AcDashboard";
import AcUseCases from "./AcUseCases";
import AcUseCaseForm from "./AcUseCaseForm";
import AcRouting from "./AcRouting";
import AcConversations from "./AcConversations";
import AcOutbound from "./AcOutbound";
import { P } from "./acPaths";

export const AI_CALLING_GROUPS = [
  { title: "AI Calling", links: [{ to: P.home, label: "Dashboard", end: true, icon: Activity }] },
  {
    title: "Inbound",
    links: [
      { to: P.usecases, label: "Use cases", icon: ListChecks },
      { to: P.routing, label: "Routing", icon: Route },
      { to: P.conversations, label: "Conversations", icon: MessageSquareText },
    ],
  },
  { title: "Outbound", links: [{ to: P.outbound, label: "Outbound", icon: PhoneOutgoing }] },
];

export const acRoutes = [
  { path: P.home, element: createElement(AcDashboard) },
  { path: P.usecases, element: createElement(AcUseCases) },
  { path: P.usecaseNew, element: createElement(AcUseCaseForm) },
  { path: `${P.usecases}/:id`, element: createElement(AcUseCaseForm) },
  { path: P.routing, element: createElement(AcRouting) },
  { path: P.conversations, element: createElement(AcConversations) },
  { path: P.outbound, element: createElement(AcOutbound) },
];
