/**
 * 科目维度定义 —— 前端唯一来源。
 * 后端对应 backend/database.py 顶部的 SUBJECTS，两边改动需同步。
 */

export const SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物"] as const;

/** 「全部科目」视图标识；传给后端时会被当成「不筛选」 */
export const SUBJECT_ALL = "全部";

export const DEFAULT_SUBJECT = "数学";

export type Subject = (typeof SUBJECTS)[number];

/** 科目主题色：用于科目 Tab、科目标签、PDF 统计条 */
export const SUBJECT_COLORS: Record<string, string> = {
  语文: "#E4572E",
  数学: "#4F46E5",
  英语: "#0EA5E9",
  物理: "#7C3AED",
  化学: "#059669",
  生物: "#16A34A",
  全部: "#6B7280",
  未分类: "#9CA3AF",
};

export function subjectColor(subject: string): string {
  return SUBJECT_COLORS[subject] ?? "#6B7280";
}

/** 科目 Tab 排序：SUBJECTS 顺序优先，未知科目（未分类）排最后 */
export function subjectOrder(subject: string): number {
  const i = (SUBJECTS as readonly string[]).indexOf(subject);
  return i === -1 ? 99 : i;
}
