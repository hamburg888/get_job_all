export type Platform = 'boss' | 'zhilian' | 'qiancheng' | 'liepin' | 'yupao'
export type ResumeSyncStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export interface PlatformOverview {
  platform: Platform
  total_jobs: number
  delivered_jobs: number
  failed_jobs: number
  active_task_status: string
  last_run_at: string | null
}

export interface TaskStatus {
  platform: Platform
  status: string
  current_step: string
  processed_count: number
  delivered_count: number
  failed_count: number
  started_at: string | null
  current_step_started_at: string | null
  finished_at: string | null
  task_run_id: number | null
  waiting_for_login: boolean
  account_id: number | null
}

export interface PreviewState {
  platform: Platform
  task_run_id: number | null
  active: boolean
  enabled: boolean
  headless_enabled: boolean
  interval_ms: number
  seq: number
  frame_url: string | null
  updated_at: string | null
  current_step: string
  message: string
}

export interface AccountItem {
  id: number
  platform: Platform
  phone: string
  username: string
  remark: string
  is_default: boolean
  is_active: boolean
  profile_dir: string
  today_delivered_count: number
  total_delivered_count: number
}

export interface AccountPayload {
  platform: Platform
  phone: string
  username: string
  remark: string
  is_default: boolean
  is_active: boolean
}

export interface JobItem {
  id: number
  platform: Platform
  platform_job_id: string
  account_id: number | null
  account_name: string | null
  title: string
  company_name: string
  salary_text: string
  salary_min: number | null
  salary_max: number | null
  city: string
  experience_text: string
  education_text: string
  description: string
  detail_url: string
  tags: string[]
  latest_status: string
  discovered_at: string
  delivered_at: string | null
  latest_reason: string | null
  latest_send_status: string | null
  latest_chat_message: string | null
}

export interface DeliveryItem {
  id: number
  platform: Platform
  account_id: number | null
  account_name: string | null
  status: string
  status_reason: string
  send_status: string
  chat_sent: boolean
  chat_message: string
  created_at: string
  job_id: number
}

export interface PaginatedResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface LogItem {
  id: number
  platform: Platform
  level: string
  event_type: string
  message: string
  created_at: string
  extra_json?: Record<string, unknown> | null
}

export interface StatsBucket {
  label: string
  value: number
}

export interface PlatformStats {
  salary_buckets: StatsBucket[]
  experience_buckets: StatsBucket[]
  city_buckets: StatsBucket[]
  keyword_buckets: StatsBucket[]
}

export interface DashboardFilters {
  account_id: number | null
  statuses: string[]
  send_statuses: string[]
  date_from: string
  date_to: string
}

export interface BossReplyRule {
  id: string
  label: string
  enabled: boolean
  keywords: string[]
  reply_template: string
}

export function createBossReplyRule(partial: Partial<BossReplyRule> = {}): BossReplyRule {
  return {
    id: partial.id || `boss-reply-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    label: partial.label ?? '',
    enabled: partial.enabled ?? true,
    keywords: Array.isArray(partial.keywords) ? [...partial.keywords] : [],
    reply_template: partial.reply_template ?? '',
  }
}

export function createDefaultBossReplyRules(): BossReplyRule[] {
  return [
    createBossReplyRule({
      id: 'resume-material',
      label: '简历资料',
      keywords: ['简历', '发简历', '发送简历', '发份简历'],
      reply_template: '好的，关于 {job_title} 的简历资料我可以继续补充给您。',
    }),
    createBossReplyRule({
      id: 'wechat-contact',
      label: '微信沟通',
      keywords: ['微信', '加微信', '互换微信', '交换微信', 'vx', 'v我'],
      reply_template: '可以的，关于 {job_title} 的详细情况我们可以继续沟通，您也可以先把联系方式发我。',
    }),
    createBossReplyRule({
      id: 'salary-interview',
      label: '薪资面试',
      keywords: ['薪资', '待遇', '面试', '到岗', '入职'],
      reply_template: '收到，关于 {job_title} 的薪资和面试安排，我这边愿意继续配合沟通。',
    }),
  ]
}

export const DEFAULT_COMPANY_BLACKLIST_TEXT =
  '青钱|软通动力|南天|睿服|中电金信|佰钧成|云链|博彦|汉克时代|柯莱特|拓保|亿达信息|纬创|微创|微澜|诚迈科技|法本|兆尹|诚迈|联合永道|新致软件|宇信科技|华为|德科|FESCO|科锐|科之锐'

export const DEFAULT_COMPANY_BLACKLIST = DEFAULT_COMPANY_BLACKLIST_TEXT.split('|')

export interface StartTaskPayload {
  platform: Platform
  account_id: number | null
  search_terms: string[]
  use_manual_entry_url: boolean
  manual_entry_url: string
  keywords: string[]
  cities: string[]
  enable_company_blacklist: boolean
  company_blacklist: string[]
  enable_job_title_blacklist: boolean
  job_title_blacklist: string[]
  salary_min: number | null
  salary_max: number | null
  salary_floor_flexible_enabled: boolean
  salary_floor_min: number | null
  salary_floor_required_max: number | null
  cooldown_days: number
  cross_account_dedupe_enabled: boolean
  dedupe_match_by_title: boolean
  dedupe_match_by_company: boolean
  headless_enabled: boolean
  live_preview_enabled: boolean
  live_preview_interval_ms: number
  delivery_interval_seconds: number
  max_deliveries_per_run: number
  auto_send_chat: boolean
  use_chat_template: boolean
  chat_template: string
  enable_common_phrases: boolean
  common_phrase_indexes: number[]
  enable_image_resume: boolean
  image_resume_path: string
  auto_agree_wechat_card: boolean
  boss_first_greeting_only: boolean
  enable_follow_up_reply: boolean
  boss_reply_rules: BossReplyRule[]
  boss_active_statuses: string[]
  remark: string
}

export interface MasterResume {
  id: number
  source_platform: string
  source_account_id: number | null
  source_label: string
  source_url: string
  full_name: string
  headline: string
  contact_summary: string
  wechat_id: string
  summary: string
  job_preferences: Record<string, unknown>
  work_experiences: Array<Record<string, unknown>>
  project_experiences: Array<Record<string, unknown>>
  education_experiences: Array<Record<string, unknown>>
  certificates: string[]
  skills: string[]
  languages: string[]
  attachments: Array<Record<string, unknown>>
  raw_snapshot: Record<string, unknown>
  imported_at: string | null
  updated_at: string | null
}

export interface ResumeImportLog {
  id: number
  source_platform: string
  source_account_id: number | null
  status: ResumeSyncStatus
  message: string
  extra_json?: Record<string, unknown> | null
  created_at: string
  finished_at: string | null
}

export interface ResumeSyncTarget {
  id: number
  target_platform: string
  target_account_id: number | null
  status: ResumeSyncStatus
  message: string
  created_at: string
  finished_at: string | null
}

export interface ResumeSyncRun {
  id: number
  master_resume_id: number | null
  status: ResumeSyncStatus
  message: string
  created_at: string
  finished_at: string | null
  targets: ResumeSyncTarget[]
}

export interface ResumeCenterData {
  master_resume: MasterResume | null
  import_logs: ResumeImportLog[]
  sync_runs: ResumeSyncRun[]
  platform_support: Record<string, { import: boolean; sync: boolean }>
}

export interface MasterResumeUpdatePayload {
  full_name: string
  headline: string
  contact_summary: string
  wechat_id: string
  summary: string
  job_preferences: Record<string, unknown>
  work_experiences: Array<Record<string, unknown>>
  project_experiences: Array<Record<string, unknown>>
  education_experiences: Array<Record<string, unknown>>
  certificates: string[]
  skills: string[]
  languages: string[]
  attachments: Array<Record<string, unknown>>
  raw_snapshot: Record<string, unknown>
}

export interface ResumeSyncFieldSelection {
  basic_info: boolean
  summary: boolean
  job_preferences: boolean
  work_experiences: boolean
  project_experiences: boolean
  education_experiences: boolean
  certificates: boolean
  skills: boolean
  languages: boolean
  attachments: boolean
}

export interface LLMConfig {
  base_url: string
  api_key_masked: string
  model: string
  temperature: number
  enabled: boolean
}

export interface LLMConfigUpdatePayload {
  base_url: string
  api_key: string
  model: string
  temperature: number
  enabled: boolean
}

export interface LLMTestResult {
  success: boolean
  message: string
}

export interface TaskRunRecord {
  id: number
  platform: string
  keyword: string
  status: string
  config_json: Record<string, unknown>
  processed_count: number
  delivered_count: number
  failed_count: number
  started_at: string | null
  finished_at: string | null
  account_name: string | null
}

export interface AIAdviceRequest {
  platform: Platform
  report_type: 'daily' | 'weekly' | 'strategy'
  date_from: string
  date_to: string | null
}

export interface AIAdvice {
  advice_text: string
  salary_suggestion: string
  timing_suggestion: string
  resume_suggestion: string
  raw_response: string
}
