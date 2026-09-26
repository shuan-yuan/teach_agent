import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { Target, Loader2, CheckCircle2, Download, RefreshCw, Sparkles, BookOpen, Eye, EyeOff, Star, ChevronDown, ChevronUp, Brain, Clock, Layers } from "lucide-react";
import { fetchStudents, generatePractice, fetchPracticeList, getPracticePdfUrl, fetchSubjectOverview } from "../api/client";
import type { Student, PracticeData, PracticeQuestion, PracticeSheet, SubjectOverview } from "../types";
import { SUBJECT_ALL, subjectColor, DEFAULT_SUBJECT, pickStudentId } from "../constants";

function unwrapS(r: any): Student[] { return Array.isArray(r) ? r : r?.students ?? []; }
function unwrapP(r: any): PracticeSheet[] { return Array.isArray(r) ? r : r?.practice_sheets ?? []; }

const levelMap: Record<string, { cls: string; stars: number }> = {
  "基础巩固": { cls: "basic", stars: 1 },
  "能力提升": { cls: "improve", stars: 2 },
  "拓展挑战": { cls: "challenge", stars: 3 },
};

interface TStep { step: string; message: string; status: string }

/** 一次生成的产物：单科生成就是一个，批量生成每科一个 */
interface GenResult {
  subject: string;
  title: string;
  pid: number | null;
  data: PracticeData | null;
  error?: string;
}

export default function PracticeGeneratorPage() {
  const [sp] = useSearchParams();
  const [students, setStudents] = useState<Student[]>([]);
  const [sid, setSid] = useState<number | "">(Number(sp.get("student")) || "");
  const [subject, setSubject] = useState<string>(sp.get("subject") || SUBJECT_ALL);
  const [errIds] = useState<number[]>(() => { const e = sp.get("errors"); return e ? e.split(",").map(Number).filter(Boolean) : []; });
  const [overview, setOverview] = useState<SubjectOverview[]>([]);
  const [phase, setPhase] = useState<"idle" | "gen" | "done">("idle");
  const [curSubj, setCurSubj] = useState("");
  const [thinking, setTh] = useState<TStep[]>([]);
  const [stream, setStream] = useState("");
  const [results, setResults] = useState<GenResult[]>([]);
  const [error, setError] = useState("");
  const [showTerm, setShowT] = useState(true);
  const [showAns, setShowAns] = useState<Set<string>>(new Set());
  const [history, setHistory] = useState<PracticeSheet[]>([]);
  const termRef = useRef<HTMLPreElement>(null);

  // 进入本页自动选中「默认学生」—— 选学生这件事由学生管理页一次性配置好，
  // 不必每次进来都手动挑。用函数式 setSid，避免覆盖用户已手动切换的选择
  // （也保证 URL 上带过来的 student 参数优先）。
  useEffect(() => {
    fetchStudents().then(r => {
      const list = unwrapS(r);
      setStudents(list);
      setSid(cur => cur || pickStudentId(list));
    }).catch(() => {});
  }, []);

  // 科目概览：决定 Tab 上有哪些科目、哪几科有错题可出题
  useEffect(() => {
    if (!sid) { setOverview([]); return; }
    fetchSubjectOverview(Number(sid)).then(r => setOverview(r.subjects ?? [])).catch(() => setOverview([]));
  }, [sid]);

  const loadHistory = useCallback(() => {
    if (sid) fetchPracticeList(Number(sid), subject).then(r => setHistory(unwrapP(r))).catch(() => {});
    else setHistory([]);
  }, [sid, subject]);
  useEffect(loadHistory, [loadHistory, phase]);

  /** 生成一科的练习并消费 SSE 流；调用方负责 try/catch */
  const runOne = async (subj: string, withErrIds: boolean): Promise<GenResult> => {
    setCurSubj(subj);
    setTh([]); setStream(""); setError("");
    const reader = await generatePractice(
      Number(sid),
      withErrIds && errIds.length ? errIds : undefined,
      subj,
    );
    const dec = new TextDecoder();
    let buf = "";
    let data: PracticeData | null = null;
    let pid: number | null = null;
    let streamErr = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n"); buf = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const d = JSON.parse(line.slice(6));
          if (d.type === "thinking") setTh(p => { const i = p.findIndex(s => s.step === d.data.step); if (i >= 0) { const c = [...p]; c[i] = d.data; return c; } return [...p, d.data]; });
          else if (d.type === "content") { setStream(p => p + d.data); if (termRef.current) termRef.current.scrollTop = termRef.current.scrollHeight; }
          else if (d.type === "result") data = d.data;
          else if (d.type === "error") streamErr = String(d.data);
          else if (d.type === "done" && d.data?.practice_id) pid = d.data.practice_id;
        } catch {}
      }
    }
    return {
      subject: subj,
      title: data?.title || `${subj}练习`,
      pid,
      data,
      error: streamErr || undefined,
    };
  };

  /** 单选一科生成 */
  const startOne = async () => {
    if (!sid) return;
    setPhase("gen"); setResults([]); setShowAns(new Set());
    try {
      const r = await runOne(subject, true);
      setResults([r]);
    } catch (e: any) {
      setResults([{ subject, title: "", pid: null, data: null, error: e?.message || "生成失败" }]);
    }
    setPhase("done");
  };

  /** 按科目批量：每个有错题的科目各出一份，一份一张 PDF */
  const startBatch = async () => {
    if (!sid) return;
    const targets = overview.filter(s => s.error_count > 0);
    if (!targets.length) { setError("该学生暂无错题记录，请先批改作业"); return; }
    setPhase("gen"); setResults([]); setError(""); setShowAns(new Set());
    const acc: GenResult[] = [];
    for (const t of targets) {
      try {
        acc.push(await runOne(t.subject, false));
      } catch (e: any) {
        acc.push({ subject: t.subject, title: "", pid: null, data: null, error: e?.message || "生成失败" });
      }
      setResults([...acc]);
    }
    setPhase("done");
  };

  const toggleAns = (key: string) => setShowAns(s => { const n = new Set(s); n.has(key) ? n.delete(key) : n.add(key); return n; });
  const stu = students.find(s => s.id === sid);

  const sum = (f: (s: SubjectOverview) => number) => overview.reduce((a, s) => a + f(s), 0);
  const tabs: SubjectOverview[] = [
    { subject: SUBJECT_ALL, homework_count: sum(s => s.homework_count), error_count: sum(s => s.error_count), practice_count: sum(s => s.practice_count) },
    ...overview,
  ];
  const curErrorCount = subject === SUBJECT_ALL ? sum(s => s.error_count) : (overview.find(s => s.subject === subject)?.error_count ?? 0);
  const subjectsWithErrors = overview.filter(s => s.error_count > 0);

  return (
    <div className="page-container" style={{ maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: "var(--text-1)", display: "flex", alignItems: "center", gap: 8 }}>
          <Target size={20} style={{ color: "var(--coral)" }} /> 分层练习生成
        </h2>
        <p style={{ fontSize: 13, color: "var(--text-3)", marginTop: 4 }}>按科目根据错题生成个性化分层练习，一份 PDF 只含一科</p>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 12, marginBottom: 16 }}>
        <div className="form-group">
          <label>选择学生</label>
          <select className="form-select" style={{ width: 220 }} value={sid} onChange={e => { setSid(e.target.value ? Number(e.target.value) : ""); setSubject(SUBJECT_ALL); setPhase("idle"); setResults([]); }}>
            <option value="">请选择</option>
            {students.map(s => <option key={s.id} value={s.id}>{s.name} · {s.grade}{s.class_name}</option>)}
          </select>
        </div>
        {stu && (
          <span style={{ background: "var(--warm-gray)", borderRadius: 8, padding: "6px 10px", fontSize: 12, color: "var(--text-3)" }}>
            错题 <strong style={{ color: "var(--coral)" }}>{stu.error_count}</strong> 道
            {errIds.length > 0 && <span style={{ marginLeft: 4, background: "var(--coral-light)", color: "var(--coral)", borderRadius: 4, padding: "0 4px", fontWeight: 600 }}>已选{errIds.length}</span>}
          </span>
        )}
      </div>

      {/* ── 科目切换：选中单科=生成该科一份；选中「全部」=可按科目批量各出一份 ── */}
      {sid && tabs.length > 1 && (
        <div className="subject-tabs">
          {tabs.map(t => {
            const active = t.subject === subject;
            const c = subjectColor(t.subject);
            return (
              <button
                key={t.subject}
                className={`subject-tab ${active ? "active" : ""}`}
                onClick={() => { setSubject(t.subject); setPhase("idle"); setResults([]); }}
                style={active ? { borderColor: c, color: c, background: `${c}14` } : undefined}
              >
                <span className="st-dot" style={{ background: c }} />
                {t.subject}
                <span className="st-count">{t.error_count}</span>
              </button>
            );
          })}
        </div>
      )}

      {phase === "idle" && sid && subject !== SUBJECT_ALL && (
        <button className="start-btn" onClick={startOne} disabled={curErrorCount === 0}>
          <Sparkles size={18} /> 生成{subject}分层练习（{curErrorCount} 道错题）
        </button>
      )}
      {phase === "idle" && sid && subject === SUBJECT_ALL && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
          <button className="start-btn" onClick={startBatch} disabled={!subjectsWithErrors.length}>
            <Layers size={18} /> 按科目批量生成（{subjectsWithErrors.length} 科各一份）
          </button>
          <button className="btn-secondary" onClick={() => setSubject(DEFAULT_SUBJECT)} style={{ minHeight: 44 }}>
            <Sparkles size={15} /> 只生成{DEFAULT_SUBJECT}
          </button>
        </div>
      )}
      {phase === "idle" && sid && subject === SUBJECT_ALL && !subjectsWithErrors.length && (
        <p style={{ padding: "16px 0", fontSize: 13, color: "var(--text-3)" }}>该学生暂无错题记录，请先批改作业</p>
      )}
      {phase === "idle" && !sid && <p style={{ padding: "40px 0", textAlign: "center", fontSize: 13, color: "var(--text-3)" }}>请先选择学生</p>}

      {phase !== "idle" && (
        <>
          {/* ── 生成进度 ── */}
          <div className="grading-panels">
            <div className="card">
              <div className="card-title">
                <Brain size={16} style={{ color: "var(--purple)" }} /> 生成思维链
                {curSubj && <span style={{ marginLeft: 8, fontSize: 12, fontWeight: 600, color: subjectColor(curSubj) }}>当前：{curSubj}</span>}
                {subject === SUBJECT_ALL && subjectsWithErrors.length > 1 && (
                  <span style={{ marginLeft: 8, fontSize: 12, color: "var(--text-3)" }}>
                    （{results.length}/{subjectsWithErrors.length} 科已完成）
                  </span>
                )}
              </div>
              <div style={{ position: "relative" }}>
                {thinking.length > 1 && <div style={{ position: "absolute", left: 15, top: 12, bottom: 12, width: 2, background: "var(--border)" }} />}
                {thinking.map((t, i) => {
                  const done = t.status === "done";
                  return (
                    <div key={i} className="thinking-step">
                      <div className={`step-dot ${done ? "done" : "active"}`}>{done ? <CheckCircle2 size={14} /> : <Loader2 size={14} className="anim-spin" />}</div>
                      <div className={`step-text ${done ? "done" : "active"}`}>{t.message}</div>
                    </div>
                  );
                })}
                {phase === "gen" && !thinking.length && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 0", fontSize: 13, color: "var(--text-3)" }}><Loader2 size={14} className="anim-spin" /> 等待智能体回复…</div>
                )}
              </div>
            </div>
            <div className="terminal-panel">
              <div className="terminal-bar">
                <div className="terminal-dots"><span /><span /><span /></div>
                <div className={`terminal-status ${phase === "done" ? "done" : ""}`}>{phase === "gen" ? "生成中" : "已完成"}</div>
                <button onClick={() => setShowT(!showTerm)} style={{ background: "none", border: "none", color: "#6c7086", cursor: "pointer" }}>{showTerm ? <ChevronUp size={13} /> : <ChevronDown size={13} />}</button>
              </div>
              {showTerm && <pre ref={termRef} className={`terminal-body ${phase === "gen" ? "stream-cursor" : ""}`}>{stream || "等待智能体输出…"}</pre>}
            </div>
          </div>

          {error && <div className="error-msg">{error}</div>}

          {/* ── 批量生成：每科一张卡片，各自下载 ── */}
          {results.length > 1 && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-title"><Layers size={16} style={{ color: "var(--coral)" }} /> 按科目生成结果</div>
              {results.map((r, i) => {
                const c = subjectColor(r.subject);
                return (
                  <div key={i} className="batch-result">
                    <span className="br-subject" style={{ background: `${c}18`, color: c }}>{r.subject}</span>
                    <span className="br-title">{r.error ? r.error : (r.data?.title || r.title)}</span>
                    <span className="br-meta">{r.data?.questions?.length ?? 0} 题</span>
                    {r.pid
                      ? <a href={getPracticePdfUrl(r.pid)} target="_blank" rel="noreferrer" className="btn-primary" style={{ textDecoration: "none", fontSize: 12 }}><Download size={13} /> PDF</a>
                      : <span className="br-meta" style={{ color: "var(--coral)" }}>失败</span>}
                  </div>
                );
              })}
            </div>
          )}

          {/* ── 单科生成：完整展开 ── */}
          {results.length === 1 && results[0].data && (() => {
            const data = results[0].data!;
            const grouped = data.questions?.reduce<Record<string, PracticeQuestion[]>>((a, q) => { (a[q.level || "基础巩固"] ??= []).push(q); return a; }, {}) ?? {};
            return (
              <>
                <div className="result-header" style={{ flexWrap: "wrap" }}>
                  <div style={{ flex: 1 }}>
                    <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-1)" }}>
                      {data.title}
                      {results[0].subject && <span className="tag" style={{ marginLeft: 8, background: `${subjectColor(results[0].subject)}18`, color: subjectColor(results[0].subject) }}>{results[0].subject}</span>}
                    </h3>
                    {data.description && <p style={{ fontSize: 13, color: "var(--text-2)", marginTop: 4 }}>{data.description}</p>}
                    {data.target_knowledge_points?.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
                        {data.target_knowledge_points.map((k, i) => <span key={i} className="tag tag-indigo">{k}</span>)}
                      </div>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    {results[0].pid && <a href={getPracticePdfUrl(results[0].pid)} target="_blank" rel="noreferrer" className="btn-primary" style={{ textDecoration: "none", fontSize: 12 }}><Download size={13} /> 下载 PDF</a>}
                    <button className="btn-secondary" onClick={() => { setPhase("idle"); setResults([]); setStream(""); setTh([]); }}><RefreshCw size={13} /> 重新生成</button>
                  </div>
                </div>

                {Object.entries(grouped).map(([lv, qs]) => {
                  const cfg = levelMap[lv] ?? levelMap["基础巩固"];
                  return (
                    <div key={lv} style={{ marginBottom: 16 }}>
                      <div className={`level-header ${cfg.cls}`}>
                        {Array.from({ length: cfg.stars }).map((_, i) => <Star key={i} size={14} fill="currentColor" />)}
                        <span className="lv-name">{lv}</span>
                        <span className="lv-count">（{qs.length}题）</span>
                      </div>
                      {qs.map(q => {
                        const key = `${results[0].subject}-${lv}-${q.id}`;
                        return (
                          <div key={key} className="practice-q">
                            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                              <span className="pq-num">{q.id}</span>
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <p className="pq-text">{q.question}</p>
                                {q.options && <div className="pq-options">{q.options.map((o, oi) => <div key={oi} className="pq-opt">{o}</div>)}</div>}
                                <div className="pq-kp" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                  {q.knowledge_point && <span className="tag tag-indigo">{q.knowledge_point}</span>}
                                  <button className="ans-toggle" onClick={() => toggleAns(key)}>
                                    {showAns.has(key) ? <><EyeOff size={11} /> 隐藏</> : <><Eye size={11} /> 答案</>}
                                  </button>
                                </div>
                                {showAns.has(key) && (
                                  <div className="ans-box">
                                    <div><strong>答案：</strong>{q.answer}</div>
                                    {q.solution && <div style={{ marginTop: 4 }}><strong>解析：</strong><span style={{ color: "var(--text-2)" }}>{q.solution}</span></div>}
                                  </div>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  );
                })}

                {data.study_suggestions && (
                  <div className="study-tip">
                    <h4><BookOpen size={14} /> 学习建议</h4>
                    <p>{data.study_suggestions}</p>
                  </div>
                )}

                <div className="ai-gen-label">以上练习题及解析内容由 AI 生成，基于国产大模型，仅供教学参考</div>
              </>
            );
          })()}

          {results.length === 1 && results[0].error && (
            <div className="error-msg" style={{ marginTop: 16 }}>{results[0].subject}：{results[0].error}</div>
          )}
        </>
      )}

      {/* ── 历史练习记录（跟随科目 Tab 筛选） ── */}
      {sid && history.length > 0 && (
        <div className="history-section">
          <h3>
            <Clock size={14} style={{ color: "var(--coral)" }} /> 历史练习记录
            {subject !== SUBJECT_ALL && <span style={{ marginLeft: 6, fontSize: 12, color: "var(--text-3)" }}>（仅 {subject}）</span>}
          </h3>
          {history.map(h => (
            <div key={h.id} className="history-item">
              <div>
                <div className="hi-title">
                  {h.subject && <span className="tag" style={{ marginRight: 6, background: `${subjectColor(h.subject)}18`, color: subjectColor(h.subject) }}>{h.subject}</span>}
                  {h.title || "练习题"}
                </div>
                <div className="hi-date">{new Date(h.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" })}</div>
              </div>
              <a href={getPracticePdfUrl(h.id)} target="_blank" rel="noreferrer"><Download size={11} /> PDF</a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
