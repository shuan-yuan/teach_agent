import { useState, useEffect } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { BookX, ChevronDown, Download, Target, Loader2, Search, CheckSquare, Square, Sparkles, TrendingUp, BookOpen, Award, AlertTriangle } from "lucide-react";
import { fetchStudents, fetchHomeworkList, fetchSubjectOverview, fetchStudentErrors, getErrorReportPdfUrl } from "../api/client";
import type { Student, ErrorRecord, ErrorStats, HomeworkSubmission, SubjectOverview } from "../types";
import { SUBJECT_ALL, subjectColor } from "../constants";

function unwrap(r: any): Student[] { return Array.isArray(r) ? r : r?.students ?? []; }

export default function ErrorAnalysisPage() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const [students, setStudents] = useState<Student[]>([]);
  const [sid, setSid] = useState<number | "">(Number(sp.get("student")) || "");
  const [subject, setSubject] = useState<string>(sp.get("subject") || SUBJECT_ALL);
  const [subjects, setSubjects] = useState<SubjectOverview[]>([]);
  const [errors, setErrors] = useState<ErrorRecord[]>([]);
  const [stats, setStats] = useState<ErrorStats | null>(null);
  const [loading, setLoad] = useState(false);
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [expanded, setExp] = useState<Set<number>>(new Set());

  // Score trend data
  const [homeworks, setHomeworks] = useState<HomeworkSubmission[]>([]);

  useEffect(() => { fetchStudents().then(r => setStudents(unwrap(r))).catch(() => {}); }, []);

  // 科目概览 + 作业列表：只在切换学生时拉一次
  useEffect(() => {
    if (!sid) { setSubjects([]); setHomeworks([]); return; }
    fetchSubjectOverview(Number(sid))
      .then(r => setSubjects(r.subjects ?? []))
      .catch(() => setSubjects([]));
    fetchHomeworkList(Number(sid))
      .then(hwList => setHomeworks(Array.isArray(hwList) ? hwList.filter((h: any) => h.status === "completed") : []))
      .catch(() => {});
  }, [sid]);

  // 错题 + 统计：学生或科目变化都要重拉（这就是「按科目分类」的核心）
  useEffect(() => {
    if (!sid) { setErrors([]); setStats(null); return; }
    setLoad(true);
    setSel(new Set());
    setExp(new Set());
    fetchStudentErrors(Number(sid), subject)
      .then(d => { setErrors(d.errors ?? []); setStats(d.stats ?? null); })
      .catch(() => { setErrors([]); setStats(null); })
      .finally(() => setLoad(false));
  }, [sid, subject]);

  const changeSid = (id: number | "") => {
    setSid(id);
    setSubject(SUBJECT_ALL);
    setSel(new Set());
    if (id) setSp({ student: String(id) }); else setSp({});
  };
  const changeSubject = (s: string) => {
    setSubject(s);
    if (sid) setSp(s === SUBJECT_ALL ? { student: String(sid) } : { student: String(sid), subject: s });
  };
  const toggle = (id: number, set: React.Dispatch<React.SetStateAction<Set<number>>>) => set(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const filtered = errors.filter(e => {
    if (!search) return true;
    const q = search.toLowerCase();
    return e.question_text?.toLowerCase().includes(q) || e.knowledge_point?.toLowerCase().includes(q) || e.error_type?.toLowerCase().includes(q);
  });

  const maxKP = Math.max(1, ...(stats?.by_knowledge_point?.map(k => k.count) ?? []));
  const maxET = Math.max(1, ...(stats?.by_error_type?.map(k => k.count) ?? []));

  // ── 科目维度 ──
  const sum = (f: (s: SubjectOverview) => number) => subjects.reduce((a, s) => a + f(s), 0);
  const tabs: SubjectOverview[] = [
    { subject: SUBJECT_ALL, homework_count: sum(s => s.homework_count), error_count: sum(s => s.error_count), practice_count: sum(s => s.practice_count) },
    ...subjects,
  ];
  const curSubject = subject === SUBJECT_ALL ? null : subjects.find(s => s.subject === subject);
  const curErrorCount = subject === SUBJECT_ALL ? sum(s => s.error_count) : (curSubject?.error_count ?? 0);
  const practiceUrl = `/practice?student=${sid}${subject === SUBJECT_ALL ? "" : `&subject=${encodeURIComponent(subject)}`}`;

  // Student info
  const stu = students.find(s => s.id === sid);
  const recentScores = homeworks.slice(0, 8).reverse(); // oldest first for trend
  const avgScore = stu?.avg_score ?? 0;

  return (
    <div className="page-container" style={{ maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", display: "flex", alignItems: "center", gap: 8 }}>
          <BookX size={20} style={{ color: "var(--coral)" }} /> 错题分析与学情画像
        </h2>
        <p style={{ fontSize: 13, color: "var(--text-3)", marginTop: 4 }}>按科目查看错题、分析知识薄弱点</p>
      </div>

      <select className="form-select" style={{ maxWidth: 280, marginBottom: 16 }} value={sid} onChange={e => changeSid(e.target.value ? Number(e.target.value) : "")}>
        <option value="">请选择学生</option>
        {students.map(s => <option key={s.id} value={s.id}>{s.name} · {s.grade}{s.class_name}</option>)}
      </select>

      {!sid && <p style={{ padding: "60px 0", textAlign: "center", fontSize: 13, color: "var(--text-3)" }}>请先选择学生</p>}

      {/* ── 科目切换 ── 批改时选了什么科目，这里就能按科目分开看 ── */}
      {sid && tabs.length > 1 && (
        <div className="subject-tabs">
          {tabs.map(t => {
            const active = t.subject === subject;
            const c = subjectColor(t.subject);
            return (
              <button
                key={t.subject}
                className={`subject-tab ${active ? "active" : ""}`}
                onClick={() => changeSubject(t.subject)}
                style={active ? { borderColor: c, color: c, background: `${c}14` } : undefined}
              >
                <span className="st-dot" style={{ background: c }} />
                {t.subject}
                <span className="st-count">{t.error_count}</span>
                {t.practice_count > 0 && <span className="st-sub">练习 {t.practice_count}</span>}
              </button>
            );
          })}
        </div>
      )}

      {loading && <div style={{ display: "flex", justifyContent: "center", padding: "60px 0" }}><Loader2 size={24} className="anim-spin" style={{ color: "var(--coral)" }} /></div>}

      {sid && !loading && (
        <>
          {/* ── Student Profile / Score Trend ── */}
          {stu && (
            <div className="profile-card">
              <div className="profile-top">
                <div className="profile-avatar" style={{ background: stu.avatar_color || "var(--coral)" }}>{stu.name[0]}</div>
                <div className="profile-info">
                  <div className="profile-name">{stu.name}</div>
                  <div className="profile-tags">
                    {stu.grade && <span>{stu.grade}</span>}
                    {stu.class_name && <span>{stu.class_name}</span>}
                    {stu.subject && <span className="subject">默认 {stu.subject}</span>}
                  </div>
                </div>
                <div className="profile-stats-row">
                  <div className="profile-stat">
                    <BookOpen size={13} style={{ color: "var(--text-3)" }} />
                    <div className="ps-val">{stu.homework_count}</div>
                    <div className="ps-lbl">作业</div>
                  </div>
                  <div className="profile-stat">
                    <Award size={13} style={{ color: "var(--text-3)" }} />
                    <div className="ps-val" style={{ color: avgScore >= 80 ? "var(--teal)" : avgScore >= 60 ? "var(--amber)" : avgScore > 0 ? "var(--coral)" : undefined }}>{avgScore}</div>
                    <div className="ps-lbl">均分</div>
                  </div>
                  <div className="profile-stat">
                    <AlertTriangle size={13} style={{ color: "var(--text-3)" }} />
                    <div className="ps-val">{stu.error_count}</div>
                    <div className="ps-lbl">错题</div>
                  </div>
                </div>
              </div>

              {recentScores.length > 0 && (
                <div className="score-trend">
                  <div className="trend-title"><TrendingUp size={14} style={{ color: "var(--coral)" }} /> 得分趋势</div>
                  <div className="trend-chart">
                    {recentScores.map((hw, i) => {
                      const score = hw.score ?? 0;
                      const color = score >= 80 ? "var(--teal)" : score >= 60 ? "var(--amber)" : "var(--coral)";
                      return (
                        <div key={i} className="trend-bar-wrap">
                          <div className="trend-bar" style={{ height: `${Math.max(score, 5)}%`, background: color }} />
                          <div className="trend-score" style={{ color }}>{score}</div>
                          <div className="trend-date">{new Date(hw.created_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── Stats Charts（统计范围跟随科目 Tab）── */}
          {stats && curErrorCount > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 20 }}>
              <StatBars title={`薄弱知识点分布${subject === SUBJECT_ALL ? "" : `（${subject}）`}`} items={stats.by_knowledge_point?.slice(0, 8)} keyField="knowledge_point" max={maxKP} color="coral" />
              <StatBars title={`错误类型分布${subject === SUBJECT_ALL ? "" : `（${subject}）`}`} items={stats.by_error_type?.slice(0, 8)} keyField="error_type" max={maxET} color="amber" />
            </div>
          )}

          {/* ── Toolbar ── */}
          <div className="toolbar">
            <div className="search-box">
              <Search size={14} />
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索题目、知识点…" />
            </div>
            <button
              className="btn-secondary"
              disabled={curErrorCount === 0}
              onClick={() => window.open(getErrorReportPdfUrl(Number(sid), subject), "_blank")}
            >
              <Download size={13} /> {subject === SUBJECT_ALL ? "错题报告 PDF" : `${subject}错题报告 PDF`}
            </button>
            <button
              className="btn-primary"
              disabled={curErrorCount === 0}
              onClick={() => nav(practiceUrl)}
            >
              <Target size={13} /> {subject === SUBJECT_ALL ? "生成练习" : `生成${subject}练习`}
            </button>
          </div>

          {/* ── Error List ── */}
          {!filtered.length ? (
            <p style={{ padding: "40px 0", textAlign: "center", fontSize: 13, color: "var(--text-3)" }}>
              {errors.length ? "没有匹配的结果" : subject === SUBJECT_ALL ? "暂无错题记录" : `${subject}暂无错题记录`}
            </p>
          ) : (
            filtered.map(err => {
              const isOpen = expanded.has(err.id);
              const isSel = sel.has(err.id);
              return (
                <div key={err.id} className={`error-item ${isSel ? "selected" : ""}`}>
                  <div className="error-item-header" onClick={() => toggle(err.id, setExp)}>
                    <button onClick={e => { e.stopPropagation(); toggle(err.id, setSel); }} style={{ background: "none", border: "none", color: "var(--text-3)", cursor: "pointer" }}>
                      {isSel ? <CheckSquare size={15} style={{ color: "var(--coral)" }} /> : <Square size={15} />}
                    </button>
                    <span className="qnum">{err.question_num}</span>
                    <span className="qtext">{err.question_text || "—"}</span>
                    {/* 「全部」视图下每条标出科目，才知道它来自哪一科 */}
                    {subject === SUBJECT_ALL && err.subject && (
                      <span className="tag" style={{ background: `${subjectColor(err.subject)}18`, color: subjectColor(err.subject) }}>{err.subject}</span>
                    )}
                    {err.knowledge_point && <span className="tag tag-indigo">{err.knowledge_point}</span>}
                    {err.error_type && <span className="tag tag-orange">{err.error_type}</span>}
                    <ChevronDown size={13} style={{ color: "var(--text-3)", transition: "transform 0.2s", transform: isOpen ? "rotate(180deg)" : "none", flexShrink: 0 }} />
                  </div>
                  {isOpen && (
                    <div className="question-detail">
                      <div className="answer-grid">
                        <div className="answer-box wrong-ans"><div className="answer-label">学生答案：</div><span>{err.student_answer}</span></div>
                        <div className="answer-box correct-ans"><div className="answer-label">正确答案：</div><span>{err.correct_answer}</span></div>
                      </div>
                      {err.analysis && <div className="analysis-box">{err.analysis}</div>}
                    </div>
                  )}
                </div>
              );
            })
          )}

          {sel.size > 0 && (
            <div className="float-action">
              <button onClick={() => nav(`${practiceUrl}&errors=${Array.from(sel).join(",")}`)}><Sparkles size={15} /> 选中 {sel.size} 道错题生成练习</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatBars({ title, items, keyField, max, color }: { title: string; items: any[]; keyField: string; max: number; color: string }) {
  return (
    <div className="stat-bars-card">
      <h3>{title}</h3>
      {items?.length ? items.map((k, i) => (
        <div key={i} className="stat-bar-row">
          <span className="stat-bar-label">{k[keyField]}</span>
          <div className="stat-bar-track"><div className={`stat-bar-fill ${color}`} style={{ width: `${(k.count / max) * 100}%` }} /></div>
          <span className="stat-bar-count">{k.count}</span>
        </div>
      )) : <p style={{ padding: "16px 0", textAlign: "center", fontSize: 13, color: "var(--text-3)" }}>暂无</p>}
    </div>
  );
}
