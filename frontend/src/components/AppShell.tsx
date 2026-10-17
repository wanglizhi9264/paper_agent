import { NavLink, Outlet } from "react-router-dom";

export function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <header className="brand">Paper RAG</header>
        <nav className="nav">
          <a href="/agent" className="nav-link">论文问答（Vue）</a>
          <NavLink to="/legacy-chat" end className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            旧版 Chat
          </NavLink>
          <NavLink to="/documents" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Documents
          </NavLink>
          <NavLink to="/collections" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Collections
          </NavLink>
          <NavLink to="/health" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Health
          </NavLink>
          <NavLink to="/gold-review" className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Gold 审视
          </NavLink>
        </nav>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
