import { useState, type ReactNode } from "react";
import { Download, Eye, EyeOff, Star, BookOpen } from "lucide-react";
import type { PracticeData, PracticeQuestion } from "../types";
import { subjectColor } from "../constants";

const levelMap: Record<string, { cls: string; stars: number }> = {
  "基础巩固": { cls: "basic", stars: 1 },
  "能力提升": { cls: "improve", stars: 2 },
  "拓展挑战": { cls: "challenge", stars: 3 },
};

interface Props {
  data: PracticeData;
  /** 科目 —— 决定标签配色 */
  subject?: string;
  /** PDF 下载地址；不传则不显示下载按钮 */
  pdfUrl?: string | null;
  /** 头部右侧附加操作（如「重新生成」） */
  actions?: ReactNode;
  /** 标题下方的范围说明（如「范围：本次批改的 3 道错题」） */
  scopeNote?: ReactNode;
}

/**
 * 分层练习结果展示 —— 练习生成页与批改详情页共用。
 *
 * 抽出来之前这段只长在练习生成页里，批改页要「就地出题」就得复制一份；
 * 两处展示一旦分叉，答案折叠、知识点标签这些细节会慢慢不一致。
 */
export default function PracticeResult({ data, subject = "", pdfUrl, actions, scopeNote }: Props) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) => setOpen(s => {
    const n = new Set(s);
    if (n.has(k)) n.delete(k); else n.add(k);
    return n;
  });

  const c = subjectColor(subject);
  const grouped = data.questions?.reduce<Record<string, PracticeQuestion[]>>((acc, q) => {
    (acc[q.level || "基础巩固"] ??= []).push(q);
    return acc;
  }, {}) ?? {};

  return (
    <>
      <div className="result-header" style={{ flexWrap: "wrap" }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ fontSize: 16, fontWeight: 700, color: "var(--text-1)" }}>
            {data.title}
            {subject && <span className="tag" style={{ marginLeft: 8, background: `${c}18`, color: c }}>{subject}</span>}
          </h3>
          {data.description && <p style={{ fontSize: 13, color: "var(--text-2)", marginTop: 4 }}>{data.description}</p>}
          {scopeNote}
          {data.target_knowledge_points?.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 8 }}>
              {data.target_knowledge_points.map((k, i) => <span key={i} className="tag tag-indigo">{k}</span>)}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {pdfUrl && (
            <a href={pdfUrl} target="_blank" rel="noreferrer" className="btn-primary" style={{ textDecoration: "none", fontSize: 12 }}>
              <Download size={13} /> 下载 PDF
            </a>
          )}
          {actions}
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
              const key = `${subject}-${lv}-${q.id}`;
              return (
                <div key={key} className="practice-q">
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                    <span className="pq-num">{q.id}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p className="pq-text">{q.question}</p>
                      {q.options && <div className="pq-options">{q.options.map((o, oi) => <div key={oi} className="pq-opt">{o}</div>)}</div>}
                      <div className="pq-kp" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        {q.knowledge_point && <span className="tag tag-indigo">{q.knowledge_point}</span>}
                        <button className="ans-toggle" onClick={() => toggle(key)}>
                          {open.has(key) ? <><EyeOff size={11} /> 隐藏</> : <><Eye size={11} /> 答案</>}
                        </button>
                      </div>
                      {open.has(key) && (
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
}
