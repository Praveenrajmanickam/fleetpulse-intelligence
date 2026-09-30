import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AiAnalyst from "./pages/AiAnalyst";
import EventDetail from "./pages/EventDetail";
import EventExplorer from "./pages/EventExplorer";
import IdleIntelligence from "./pages/IdleIntelligence";
import Overview from "./pages/Overview";
import Scenario from "./pages/Scenario";
import SessionDetail from "./pages/SessionDetail";
import Sessions from "./pages/Sessions";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="idle" element={<IdleIntelligence />} />
          <Route path="idle/events" element={<EventExplorer />} />
          <Route path="idle/events/:eventId" element={<EventDetail />} />
          <Route path="idle/scenario" element={<Scenario />} />
          <Route path="sessions" element={<Sessions />} />
          <Route path="sessions/:sessionId" element={<SessionDetail />} />
          <Route path="ai" element={<AiAnalyst />} />
          <Route path="*" element={<div className="page"><h1>Not found</h1></div>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
