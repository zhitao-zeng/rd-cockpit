import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { AuthGate } from "./components/AuthGate";
import { SimpleOverview } from "./pages/SimpleOverview";

const ResearchLog = lazy(() => import("./pages/ResearchLog").then((value) => ({ default: value.ResearchLog })));
const SimpleAnalytics = lazy(() => import("./pages/SimpleAnalytics").then((value) => ({ default: value.SimpleAnalytics })));
const SimpleKnowledge = lazy(() => import("./pages/SimpleKnowledge").then((value) => ({ default: value.SimpleKnowledge })));
const SimpleReports = lazy(() => import("./pages/SimpleReports").then((value) => ({ default: value.SimpleReports })));
const ResearchRadar = lazy(() => import("./pages/ResearchRadar").then((value) => ({ default: value.ResearchRadar })));
const Development = lazy(() => import("./pages/Development").then((value) => ({ default: value.Development })));
const ProjectIntelligence = lazy(() => import("./pages/ProjectIntelligence").then((value) => ({ default: value.ProjectIntelligence })));
const AlgorithmArchitecture = lazy(() => import("./pages/AlgorithmArchitecture").then((value) => ({ default: value.AlgorithmArchitecture })));
const Experiments = lazy(() => import("./pages/Experiments").then((value) => ({ default: value.Experiments })));

function LoadingPage() {
  return <div className="card muted">正在加载这一页…</div>;
}

export function App() {
  return (
    <AuthGate><Routes>
      <Route element={<Layout />}>
        <Route index element={<SimpleOverview />} />
        <Route path="records" element={<Suspense fallback={<LoadingPage />}><ResearchLog /></Suspense>} />
        <Route path="development" element={<Suspense fallback={<LoadingPage />}><Development /></Suspense>} />
        <Route path="intelligence" element={<Suspense fallback={<LoadingPage />}><ProjectIntelligence /></Suspense>} />
        <Route path="architecture" element={<Suspense fallback={<LoadingPage />}><AlgorithmArchitecture /></Suspense>} />
        <Route path="experiments" element={<Suspense fallback={<LoadingPage />}><Experiments /></Suspense>} />
        <Route path="analytics" element={<Suspense fallback={<LoadingPage />}><SimpleAnalytics /></Suspense>} />
        <Route path="knowledge" element={<Suspense fallback={<LoadingPage />}><SimpleKnowledge /></Suspense>} />
        <Route path="radar" element={<Suspense fallback={<LoadingPage />}><ResearchRadar /></Suspense>} />
        <Route path="reports" element={<Suspense fallback={<LoadingPage />}><SimpleReports /></Suspense>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes></AuthGate>
  );
}
