// ============================================================
// Auth
// ============================================================

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  is_admin: boolean;
}

// ============================================================
// API Configuration
// ============================================================

/**
 * 注意：完整 API Key 永远不下发到前端，只返回掩码。
 * 保存时若用户没改 Key，回传 `mask_sentinel` 表示保留服务器上的原值。
 */
export interface ApiConfig {
  endpoint: string;
  model_name: string;
  api_key_masked: string;
  has_api_key: boolean;
  is_configured: boolean;
  mask_sentinel: string;
}

// ============================================================
// Students
// ============================================================

export interface Student {
  id: number;
  name: string;
  grade: string;
  class_name: string;
  subject: string;
  avatar_color: string;
  created_at: string;
  /** 默认学生：1 = 进入作业批改 / 错题分析 / 练习生成时自动选中（同一用户唯一） */
  is_default: number;
  homework_count: number;
  avg_score: number;
  error_count: number;
}

// ============================================================
// Homework & Grading
// ============================================================

export interface HomeworkSubmission {
  id: number;
  student_id: number;
  student_name: string;
  subject: string;
  image_paths: string[];
  grading_result: GradingResult | string;
  thinking_chain: string;
  score: number;
  total_questions: number;
  correct_count: number;
  status: string;
  created_at: string;
  /**
   * 这次批改落库的错题 id 列表（详情接口返回）。
   * 前端用它显示「本次 N 道错题可出题」，也是「按这次错题生成练习」的范围依据。
   */
  error_ids?: number[];
}

export interface GradingResult {
  total_questions: number;
  correct_count: number;
  score: number;
  questions: QuestionResult[];
  overall_comment: string;
  weak_points: string[];
  /** 模型判定的科目（批改结果里带回，用于科目标签配色与归档） */
  subject?: string;
  /** 模型输出被长度上限截断，结果只包含已完成的题目 */
  truncated?: boolean;
}

export interface QuestionResult {
  question_num: number;
  question_text: string;
  student_answer: string;
  correct_answer: string;
  is_correct: boolean;
  error_type: string;
  knowledge_point: string;
  analysis: string;
  difficulty: string;
}

// ============================================================
// Error Records & Stats
// ============================================================

export interface ErrorRecord {
  id: number;
  student_id: number;
  homework_id: number;
  question_num: number;
  question_text: string;
  error_type: string;
  knowledge_point: string;
  student_answer: string;
  correct_answer: string;
  analysis: string;
  difficulty: string;
  created_at: string;
  subject: string;
  homework_date: string;
}

export interface ErrorStats {
  by_knowledge_point: Array<{ knowledge_point: string; count: number }>;
  by_error_type: Array<{ error_type: string; count: number }>;
  by_difficulty: Array<{ difficulty: string; count: number }>;
}

// ============================================================
// Practice Sheets
// ============================================================

export interface PracticeSheet {
  id: number;
  student_id: number;
  student_name: string;
  /** 该练习所属科目 —— 按科目生成与按科目筛选的依据 */
  subject: string;
  title: string;
  questions: string;
  target_knowledge_points: string;
  pdf_path: string;
  created_at: string;
}

/** 科目概览：某学生各科目的作业数 / 错题数 / 练习数（前端科目 Tab 用） */
export interface SubjectOverview {
  subject: string;
  homework_count: number;
  error_count: number;
  practice_count: number;
}

export interface PracticeData {
  title: string;
  description: string;
  target_knowledge_points: string[];
  questions: PracticeQuestion[];
  study_suggestions: string;
}

export interface PracticeQuestion {
  id: number;
  level: string;
  level_en: string;
  question: string;
  options: string[] | null;
  answer: string;
  solution: string;
  knowledge_point: string;
  difficulty: string;
}

// ============================================================
// Thinking & Dashboard
// ============================================================

export interface ThinkingStep {
  step: number;
  message: string;
  status: 'active' | 'done';
}

export interface DashboardStats {
  total_students: number;
  total_homeworks: number;
  total_errors: number;
  total_practices: number;
  avg_score: number;
  recent_activities: any[];
}

// ============================================================
// SSE Events
// ============================================================

export interface SSEEvent {
  type: 'thinking' | 'content' | 'result' | 'error' | 'done';
  data: any;
}

// ============================================================
// API Config Helpers
// ============================================================

export interface NormalizeUrlResult {
  url: string;
  tips: string;
}

export interface ValidateKeyResult {
  valid: boolean;
  message: string;
}

export interface ModelInfo {
  id: string;
  name?: string;
}

export interface FetchModelsResult {
  models: ModelInfo[];
  message: string;
}
