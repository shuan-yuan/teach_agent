/**
 * 科目维度定义 —— 前端唯一来源。
 * 后端对应 backend/database.py 顶部的 SUBJECTS，两边改动需同步。
 */

import type { Student } from "./types";

export const SUBJECTS = ["语文", "数学", "英语", "物理", "化学", "生物"] as const;

/** 「全部科目」视图标识；传给后端时会被当成「不筛选」 */
export const SUBJECT_ALL = "全部";

export const DEFAULT_SUBJECT = "数学";

/** 「自动识别」：批改页学科下拉的默认档 —— 让模型按作业内容判科，不预填学生默认学科 */
export const SUBJECT_AUTO = "自动识别";

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

/**
 * 决定进入某个模块时默认选中哪个学生。
 *
 * 优先级：默认学生 > 唯一的学生 > 空（需手动选择）。
 * 「唯一的学生」这一档是为了照顾「只有一个孩子但忘了勾默认」的情况；
 * 有多个学生且没设默认时保持空 —— 宁可让用户选一次，也不要猜错人。
 *
 * URL 上显式带来的 student（如错题分析 →「生成练习」的跳转）由调用方的
 * `cur || pickStudentId(list)` 兜住，不会被这里覆盖。
 */
export function pickStudentId(students: Student[]): number | "" {
  if (!students.length) return "";
  const def = students.find(s => Number(s.is_default) === 1);
  if (def) return def.id;
  return students.length === 1 ? students[0].id : "";
}
