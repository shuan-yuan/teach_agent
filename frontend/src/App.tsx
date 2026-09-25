import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import Students from './pages/Students'
import Grading from './pages/Grading'
import ErrorAnalysis from './pages/ErrorAnalysis'
import PracticeGenerator from './pages/PracticeGenerator'
import Login from './pages/Login'
import { fetchMe, logout as apiLogout, setUnauthorizedHandler } from './api/client'
import type { AuthUser } from './types'

function App() {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [checking, setChecking] = useState(true)

  // 全局 401 处理：任何请求遇到会话失效，统一退回登录页
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
    return () => setUnauthorizedHandler(null)
  }, [])

  // 首屏恢复会话
  useEffect(() => {
    fetchMe()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecking(false))
  }, [])

  const handleLogout = async () => {
    try {
      await apiLogout()
    } catch {
      // 登出失败也要清本地状态，否则用户会卡在已登录的假象里
    }
    setUser(null)
  }

  if (checking) {
    return (
      <div className="app-boot">
        <div className="app-boot-spinner" />
        <p>正在载入…</p>
      </div>
    )
  }

  return (
    <BrowserRouter>
      {user ? (
        <Routes>
          <Route element={<Layout user={user} onLogout={handleLogout} />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/students" element={<Students />} />
            <Route path="/grading" element={<Grading />} />
            <Route path="/errors" element={<ErrorAnalysis />} />
            <Route path="/practice" element={<PracticeGenerator />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      ) : (
        <Routes>
          <Route path="*" element={<Login onSuccess={setUser} />} />
        </Routes>
      )}
    </BrowserRouter>
  )
}

export default App
