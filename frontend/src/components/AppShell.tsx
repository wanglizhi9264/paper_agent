import { NavLink, Outlet } from "react-router-dom";

export function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <header className="brand">Paper RAG</header>
        <nav className="nav">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
            Chat
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
        </nav>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
