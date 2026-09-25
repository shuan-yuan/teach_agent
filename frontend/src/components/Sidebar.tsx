import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, Settings, Users, PenLine, BookX, Target,
  GraduationCap, X, LogOut,
} from "lucide-react";
import type { AuthUser } from "../types";

const mainNav = [
  { to: "/",         icon: LayoutDashboard, label: "工作台",   end: true },
  { to: "/students", icon: Users,           label: "学生管理" },
  { to: "/grading",  icon: PenLine,         label: "作业批改" },
  { to: "/errors",   icon: BookX,           label: "错题分析" },
  { to: "/practice", icon: Target,          label: "练习生成" },
];

const bottomNav = [
  { to: "/settings", icon: Settings, label: "API 配置" },
];

export default function Sidebar({
  user,
  onLogout,
  onClose,
}: {
  user?: AuthUser;
  onLogout?: () => void;
  onClose?: () => void;
}) {
  const initial = (user?.display_name || user?.username || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="sidebar-inner">
      {/* Decorative circles */}
      <div className="sidebar-blob sidebar-blob-1" />
      <div className="sidebar-blob sidebar-blob-2" />

      {/* Brand */}
      <div className="brand">
        <div className="brand-icon">
          <GraduationCap size={26} />
        </div>
        <h1>教育智能体</h1>
        <p>智能作业批改系统</p>
        {onClose && (
          <button className="sidebar-close" onClick={onClose} aria-label="关闭菜单">
            <X size={16} />
          </button>
        )}
      </div>

      <div className="brand-divider" />

      {/* Main Navigation */}
      <nav className="sidebar-nav">
        {mainNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onClose}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            <item.icon size={20} style={{ opacity: 0.85 }} />
            {item.label}
          </NavLink>
        ))}

        <div className="sidebar-nav-divider" />

        {bottomNav.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onClose}
            className={({ isActive }) => (isActive ? "active" : "")}
          >
            <item.icon size={20} style={{ opacity: 0.85 }} />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Current user + logout */}
      {user && (
        <div className="sidebar-user">
          <div className="sidebar-user-avatar">{initial}</div>
          <div className="sidebar-user-info">
            <strong>{user.display_name}</strong>
            <span>@{user.username}</span>
          </div>
          {onLogout && (
            <button className="sidebar-logout" onClick={onLogout} title="退出登录">
              <LogOut size={16} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
