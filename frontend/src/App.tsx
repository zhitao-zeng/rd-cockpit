import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { SimpleOverview } from "./pages/SimpleOverview";
import { ResearchLog } from "./pages/ResearchLog";
import { SimpleAnalytics } from "./pages/SimpleAnalytics";
import { SimpleKnowledge } from "./pages/SimpleKnowledge";
import { SimpleReports } from "./pages/SimpleReports";
import { ResearchRadar } from "./pages/ResearchRadar";
import { Development } from "./pages/Development";
import { ProjectIntelligence } from "./pages/ProjectIntelligence";
import { AlgorithmArchitecture } from "./pages/AlgorithmArchitecture";
import { Experiments } from "./pages/Experiments";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<SimpleOverview />} />
        <Route path="records" element={<ResearchLog />} />
        <Route path="development" element={<Development />} />
        <Route path="intelligence" element={<ProjectIntelligence />} />
        <Route path="architecture" element={<AlgorithmArchitecture />} />
        <Route path="experiments" element={<Experiments />} />
        <Route path="analytics" element={<SimpleAnalytics />} />
        <Route path="knowledge" element={<SimpleKnowledge />} />
        <Route path="radar" element={<ResearchRadar />} />
        <Route path="reports" element={<SimpleReports />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
