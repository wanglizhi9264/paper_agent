import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { HealthPage } from "./pages/HealthPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { CollectionsPage } from "./pages/CollectionsPage";
import { ChatPage } from "./pages/ChatPage";
import { GoldReviewPage } from "./pages/GoldReviewPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, refetchOnWindowFocus: false },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/documents" element={<DocumentsPage />} />
            <Route path="/collections" element={<CollectionsPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/gold-review" element={<GoldReviewPage />} />
            <Route path="*" element={<ChatPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
