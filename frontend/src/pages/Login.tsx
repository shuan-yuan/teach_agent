import { useEffect, useState } from "react";
import { GraduationCap, Loader2, AlertTriangle, LogIn, UserPlus, Info } from "lucide-react";
import {
  login as apiLogin,
  register as apiRegister,
  fetchMe,
  setSuppressAuthRedirect,
} from "../api/client";
import type { AuthUser } from "../types";

export default function Login({ onSuccess }: { onSuccess: (u: AuthUser) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [isFirstUser, setIsFirstUser] = useState(false);

  // 登录页自身不该因为 401 再跳登录页
  useEffect(() => {
    setSuppressAuthRedirect(true);
    return () => setSuppressAuthRedirect(false);
  }, []);

  // 探测是否还没有任何账号 —— 首个注册者自动成为管理员
  useEffect(() => {
    fetchMe()
      .then((u) => onSuccess(u))
      .catch(() => {
        // 未登录是预期结果，忽略
      });
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");

    const name = username.trim();
    if (!name || !password) {
      setErr("请填写用户名和密码");
      return;
    }
    if (mode === "register") {
      if (password.length < 6) {
        setErr("密码至少 6 位");
        return;
      }
      if (password !== password2) {
        setErr("两次输入的密码不一致");
        return;
      }
    }

    setBusy(true);
    try {
      const r =
        mode === "login"
          ? await apiLogin(name, password)
          : await apiRegister(name, password, displayName.trim());
      if (mode === "register" && (r as any).is_admin) {
        setIsFirstUser(true);
      }
      onSuccess(r.user);
    } catch (e: any) {
      setErr(e?.message || "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const tab = (m: "login" | "register", label: string) => (
    <button
      type="button"
      className={`auth-tab ${mode === m ? "active" : ""}`}
      onClick={() => {
        setMode(m);
        setErr("");
      }}
    >
      {label}
    </button>
  );

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="auth-brand-icon">
            <GraduationCap size={30} />
          </div>
          <h1>教育智能体</h1>
          <p>智能作业批改与分层练习系统</p>
        </div>

        <div className="auth-tabs">
          {tab("login", "登录")}
          {tab("register", "注册")}
        </div>

        <form onSubmit={submit} className="auth-form">
          <div className="auth-field">
            <label>用户名</label>
            <input
              className="auth-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="中文、字母、数字或下划线"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>

          {mode === "register" && (
            <div className="auth-field">
              <label>昵称（选填）</label>
              <input
                className="auth-input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="显示名称，默认与用户名相同"
              />
            </div>
          )}

          <div className="auth-field">
            <label>密码</label>
            <input
              className="auth-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "register" ? "至少 6 位" : "请输入密码"}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </div>

          {mode === "register" && (
            <div className="auth-field">
              <label>确认密码</label>
              <input
                className="auth-input"
                type="password"
                value={password2}
                onChange={(e) => setPassword2(e.target.value)}
                placeholder="再输入一次"
                autoComplete="new-password"
              />
            </div>
          )}

          {err && (
            <div className="auth-error">
              <AlertTriangle size={14} /> {err}
            </div>
          )}

          <button type="submit" className="auth-submit" disabled={busy}>
            {busy ? (
              <Loader2 size={16} className="anim-spin" />
            ) : mode === "login" ? (
              <LogIn size={16} />
            ) : (
              <UserPlus size={16} />
            )}
            {mode === "login" ? "登录" : "注册并进入"}
          </button>
        </form>

        {isFirstUser && (
          <div className="auth-note">
            <Info size={14} /> 你已创建首个账号，请妥善保存密码。
          </div>
        )}

        <div className="auth-foot">
          <p>每位用户的数据相互独立，互不可见。</p>
        </div>
      </div>
    </div>
  );
}
