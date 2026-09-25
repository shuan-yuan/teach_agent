import { useState, useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Menu, GraduationCap } from "lucide-react";
import Sidebar from "./Sidebar";
import type { AuthUser } from "../types";

export default function Layout({
  user,
  onLogout,
}: {
  user: AuthUser;
  onLogout: () => void;
}) {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setOpen(false), [pathname]);

  return (
    <>
      {/* ── Desktop sidebar ── */}
      <aside className="sidebar">
        <Sidebar user={user} onLogout={onLogout} />
      </aside>

      {/* ── Mobile drawer ── */}
      <div className={`mobile-overlay ${open ? "open" : ""}`}>
        <div className="backdrop" onClick={() => setOpen(false)} />
        <div className="drawer">
          <Sidebar user={user} onLogout={onLogout} onClose={() => setOpen(false)} />
        </div>
      </div>

      {/* ── Main column ── */}
      <div className="main-col">
        {/* Mobile top bar */}
        <div className="mobile-header">
          <button onClick={() => setOpen(true)} aria-label="打开菜单">
            <Menu size={20} />
          </button>
          <GraduationCap size={20} style={{ color: "var(--coral)" }} />
          <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text-1)" }}>教育智能体</span>
          <span className="mobile-header-user">{user.display_name}</span>
        </div>

        {/* Page content */}
        <div className="main-content">
          <Outlet />
        </div>
      </div>
    </>
  );
}
