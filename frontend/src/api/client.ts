import type {
  ApiConfig,
  AuthUser,
  Student,
  HomeworkSubmission,
  ErrorRecord,
  ErrorStats,
  PracticeSheet,
  SubjectOverview,
  DashboardStats,
} from '../types';
import { SUBJECT_ALL } from '../constants';

// Base URL is empty — dev server proxy / production same-origin both serve /api.
const BASE = '';

// ============================================================
// Session / 401 handling
// ============================================================

/**
 * 会话失效时的全局回调，由 App 注册（用于跳转登录页）。
 * 放在模块级而不是 React state，原因和批改会话一样：
 * 任何组件发起请求都能触发，不需要层层传参。
 */
let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null) {
  unauthorizedHandler = fn;
}

/** 标记该请求的 401 不要触发全局跳转（如登录页自身的探测请求） */
let suppressRedirect = false;
export function setSuppressAuthRedirect(v: boolean) {
  suppressRedirect = v;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function handleUnauthorized() {
  if (!suppressRedirect && unauthorizedHandler) unauthorizedHandler();
}

// ============================================================
// Helpers
// ============================================================

async function parseError(res: Response): Promise<string> {
  const body = await res.json().catch(() => null);
  const detail = body?.detail ?? body?.message ?? res.statusText;
  // FastAPI 校验错误的 detail 是数组，转成可读文本
  if (Array.isArray(detail)) {
    return detail.map((d: any) => d?.msg ?? JSON.stringify(d)).join('; ');
  }
  return String(detail);
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    // 会话靠 HttpOnly Cookie 传递，跨端口调试 / 同源生产都要显式带上
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers as Record<string, string>),
    },
    ...options,
  });

  if (res.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, '登录已过期，请重新登录');
  }

  if (!res.ok) {
    throw new ApiError(res.status, await parseError(res));
  }

  return res.json() as Promise<T>;
}

async function post<T>(path: string, data?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: data !== undefined ? JSON.stringify(data) : undefined,
  });
}

async function put<T>(path: string, data?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    body: data !== undefined ? JSON.stringify(data) : undefined,
  });
}

async function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: 'DELETE' });
}

// ============================================================
// Auth
// ============================================================

export async function register(
  username: string,
  password: string,
  displayName?: string,
): Promise<{ success: boolean; user: AuthUser; is_admin: boolean }> {
  return post('/api/auth/register', {
    username,
    password,
    display_name: displayName ?? '',
  });
}

export async function login(
  username: string,
  password: string,
): Promise<{ success: boolean; user: AuthUser }> {
  return post('/api/auth/login', { username, password });
}

export async function logout(): Promise<{ success: boolean }> {
  return post('/api/auth/logout');
}

export async function fetchMe(): Promise<AuthUser> {
  const data = await request<{ user: AuthUser }>('/api/auth/me');
  return data.user;
}

// ============================================================
// API Configuration
// ============================================================

export async function fetchConfig(): Promise<ApiConfig> {
  return request<ApiConfig>('/api/config');
}

export async function saveConfig(data: {
  endpoint: string;
  api_key: string;
  model_name: string;
}): Promise<{ success: boolean; message: string }> {
  return post('/api/config', data);
}

export async function testConfig(): Promise<{ success: boolean; message: string }> {
  return post('/api/config/test');
}

export async function normalizeUrl(
  url: string,
): Promise<{ url: string; tips: string }> {
  return post('/api/config/normalize-url', { url });
}

export async function validateKey(
  endpoint: string,
  apiKey: string,
): Promise<{ valid: boolean; message: string }> {
  return post('/api/config/validate-key', { endpoint, api_key: apiKey });
}

export async function fetchModels(
  endpoint: string,
  apiKey: string,
): Promise<{ models: { id: string; name?: string }[]; message: string }> {
  return post('/api/config/models', { endpoint, api_key: apiKey });
}

// ============================================================
// Students
// ============================================================

export async function fetchStudents(): Promise<Student[]> {
  const data = await request<any>('/api/students');
  return data?.students ?? data ?? [];
}

export async function createStudent(
  data: Pick<Student, 'name' | 'grade' | 'class_name' | 'subject'>,
): Promise<{ success: boolean; id: number; message: string }> {
  return post('/api/students', data);
}

export async function updateStudent(
  id: number,
  data: Partial<Pick<Student, 'name' | 'grade' | 'class_name' | 'subject'>>,
): Promise<{ success: boolean; message: string }> {
  return put(`/api/students/${id}`, data);
}

export async function deleteStudent(
  id: number,
): Promise<{ success: boolean; message: string }> {
  return del(`/api/students/${id}`);
}

/**
 * 设为 / 取消默认学生。后端保证同一用户下最多一个默认。
 * 取消后，作业批改 / 错题分析 / 练习生成 会回到「需手动选择学生」。
 */
export async function setDefaultStudent(
  id: number,
  isDefault: boolean,
): Promise<{ success: boolean; is_default: boolean; message: string }> {
  return put(`/api/students/${id}/default`, { is_default: isDefault });
}

// ============================================================
// Homework
// ============================================================

/**
 * Upload homework files or raw text for a student.
 * The FormData carries `student_id`, optional `content_text`, and optional files.
 * Content-Type is intentionally omitted so the browser sets the multipart boundary.
 */
export async function uploadHomework(formData: FormData): Promise<{
  success: boolean;
  homework_id: number;
  image_count: number;
  message: string;
  parse_result?: {
    mode: string;
    file_type: string;
    page_count: number;
    image_count: number;
    text_length: number;
  };
}> {
  const res = await fetch(`${BASE}/api/homework/upload`, {
    method: 'POST',
    body: formData,
    credentials: 'include',
    // Do NOT set Content-Type — browser handles multipart boundary.
  });

  if (res.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, '登录已过期，请重新登录');
  }
  if (!res.ok) {
    throw new ApiError(res.status, await parseError(res));
  }

  return res.json();
}

/**
 * Returns the SSE endpoint URL for grading a homework submission.
 * Use with `new EventSource(url)` on the caller side.
 */
export function gradeHomeworkUrl(homeworkId: number): string {
  return `${BASE}/api/homework/grade/${homeworkId}`;
}

export async function fetchHomeworkList(
  studentId?: number,
): Promise<HomeworkSubmission[]> {
  const query = studentId !== undefined ? `?student_id=${studentId}` : '';
  const data = await request<any>(`/api/homework${query}`);
  return data?.homeworks ?? data ?? [];
}

export async function fetchHomeworkDetail(
  id: number,
): Promise<HomeworkSubmission> {
  return request<HomeworkSubmission>(`/api/homework/${id}`);
}

export async function deleteHomework(
  id: number,
): Promise<{ success: boolean; message: string }> {
  return del(`/api/homework/${id}`);
}

// ============================================================
// Error Records
// ============================================================

export async function fetchStudentErrors(
  studentId: number,
  subject?: string,
): Promise<{ errors: ErrorRecord[]; stats: ErrorStats }> {
  const q = subject && subject !== SUBJECT_ALL
    ? `?subject=${encodeURIComponent(subject)}`
    : '';
  return request<{ errors: ErrorRecord[]; stats: ErrorStats }>(
    `/api/students/${studentId}/errors${q}`,
  );
}

/** 科目概览：该学生各科目的作业数 / 错题数 / 练习数 */
export async function fetchSubjectOverview(
  studentId: number,
): Promise<{ subjects: SubjectOverview[]; all_subjects: string[] }> {
  return request<{ subjects: SubjectOverview[]; all_subjects: string[] }>(
    `/api/students/${studentId}/subjects`,
  );
}

// ============================================================
// Practice Sheets
// ============================================================

/**
 * Generate a practice sheet via a POST request that returns an SSE stream.
 * Because `EventSource` only supports GET, we use `fetch` and return the
 * `ReadableStreamDefaultReader` so callers can consume chunks manually.
 */
export async function generatePractice(
  studentId: number,
  errorIds?: number[],
  subject?: string,
): Promise<ReadableStreamDefaultReader<Uint8Array>> {
  const res = await fetch(`${BASE}/api/practice/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      student_id: studentId,
      ...(errorIds !== undefined && { error_ids: errorIds }),
      // 「全部」视图不带 subject，由后端决定兜底科目
      ...(subject && subject !== SUBJECT_ALL && { subject }),
    }),
  });

  if (res.status === 401) {
    handleUnauthorized();
    throw new ApiError(401, '登录已过期，请重新登录');
  }
  if (!res.ok) {
    throw new ApiError(res.status, await parseError(res));
  }
  if (!res.body) {
    throw new Error('Response body is not a ReadableStream');
  }

  return res.body.getReader();
}

export async function fetchPracticeList(
  studentId?: number,
  subject?: string,
): Promise<PracticeSheet[]> {
  const qs = new URLSearchParams();
  if (studentId !== undefined) qs.set('student_id', String(studentId));
  if (subject && subject !== SUBJECT_ALL) qs.set('subject', subject);
  const query = qs.toString() ? `?${qs}` : '';
  const data = await request<any>(`/api/practice${query}`);
  return data?.practice_sheets ?? data ?? [];
}

/**
 * Returns the URL to download a practice sheet PDF.
 */
export function getPracticePdfUrl(practiceId: number): string {
  return `${BASE}/api/practice/${practiceId}/pdf`;
}

// ============================================================
// Reports
// ============================================================

/**
 * Returns the URL to download an error-analysis report PDF for a student.
 */
export function getErrorReportPdfUrl(studentId: number, subject?: string): string {
  const q = subject && subject !== SUBJECT_ALL
    ? `?subject=${encodeURIComponent(subject)}`
    : '';
  return `${BASE}/api/students/${studentId}/error-report-pdf${q}`;
}

// ============================================================
// Dashboard
// ============================================================

export async function fetchStats(): Promise<DashboardStats> {
  return request<DashboardStats>('/api/stats');
}
