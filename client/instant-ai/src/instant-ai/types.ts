export interface FinanceItem {
  id: number;
  title: string;
  translated_title?: string | null;
  translation_provider?: string | null;
  thumbnail_url: string;
  url: string;
  summary: string;
  published_at: string | null;
  first_seen_at: string;
  importance_score: number;
  trust_level: number;
  topics: string[];
  entities: string[];
  event_type: string;
  source_count: number;
  sources?: string[];
  is_saved: boolean;
  is_read: boolean;
}

export interface Evidence {
  id: string;
  source_name: string;
  fetched_at: string;
  content_hash: string;
}

export interface FinanceItemDetail extends FinanceItem {
  evidence: Evidence[];
  ai_job: {
    status: string;
    provider: string | null;
    model: string | null;
  } | null;
}

export interface SourceStatus {
  id: number;
  name: string;
  url: string;
  trust_level: number;
  enabled: boolean;
  last_success_at: string | null;
  last_error: string | null;
  last_item_count: number;
  topic_hints: string[];
}

export interface AppStatus {
  items: { total: number; unread: number; saved: number; last_seen: string | null };
  sources: { total: number; enabled: number; errors: number };
  collection: { running: boolean; last_result: unknown; mode: 'automatic'; interval_seconds: number };
  notifications: { pending: number };
  database_path: string;
  library_path: string;
  latest_backup: string | null;
  retention: {
    ordinary_hours: number;
    important_days: number;
    critical_days: number;
    archive_enabled: boolean;
  };
}

export interface AuthStatus {
  required: boolean;
  authenticated: boolean;
  setup_required: boolean;
  username: string;
  expires_at: number | null;
  session_days: number;
}

export interface AuthLoginResult {
  ok: boolean;
  required?: boolean;
  username?: string;
  expires_at?: number;
}

export type BloggerTransferStatus =
  | 'pending'
  | 'manifest_received'
  | 'transferring'
  | 'verifying'
  | 'verified'
  | 'failed';

export type BloggerProcessingStatus =
  | 'awaiting_transfer'
  | 'awaiting_asr_approval'
  | 'transcribing'
  | 'ready'
  | 'failed';

export interface BloggerStatusCounts {
  creators: number;
  works: number;
  transferring: number;
  awaiting_asr_approval: number;
  ready: number;
  failed: number;
}

export interface BloggerLibraryStatus {
  available: boolean;
  module: 'blogger-library';
  mode: 'owner-mobile-library';
  message: string;
  counts: BloggerStatusCounts;
}

export interface BloggerCreatorStatusCounts {
  works: number;
  transferring: number;
  awaiting_asr_approval: number;
  ready: number;
  failed: number;
}

export interface BloggerCreator {
  creator_id: string;
  display_name: string;
  platform: string;
  work_count: number;
  latest_published_at: string | null;
  latest_captured_at: string | null;
  status_counts: BloggerCreatorStatusCounts;
}

export interface BloggerCreatorsResponse {
  items: BloggerCreator[];
  count: number;
}

export interface BloggerTransferSummary {
  status: BloggerTransferStatus;
  source_revision: number;
  received_at: string | null;
  media_expected: number;
  media_received: number;
  comments_expected: number;
  comments_received: number;
}

export interface BloggerWork {
  work_key: string;
  creator_id: string;
  source_work_id: string;
  platform: string;
  work_type: string;
  title: string;
  description: string;
  source_url: string;
  published_at: string | null;
  captured_at: string;
  transfer: BloggerTransferSummary;
  processing_status: BloggerProcessingStatus;
  media_available: boolean;
  video_url: string;
  has_video_text: boolean;
  comment_count: number;
}

export interface BloggerCreatorWorksResponse {
  creator: BloggerCreator;
  items: BloggerWork[];
  count: number;
}

export interface BloggerCommentSnapshot {
  captured_at: string | null;
  complete: boolean;
  expected_total: number;
  captured_count: number;
  top_level_count: number;
  reply_groups: number;
  missing_replies: number;
}

export interface BloggerWorkDetail extends BloggerWork {
  comment_snapshot: BloggerCommentSnapshot | null;
  video_text: { text: string; official: boolean; source: string; updated_at: string };
  transcripts: ModelMrTranscript[];
  comments: ModelMrComment[];
  comment_total: number;
  capabilities: {
    video: boolean;
    save_title: boolean;
    save_video_text: boolean;
    transcribe_video: boolean;
    doubao_asr: boolean;
    comments: boolean;
  };
}

export interface ModelMrStatus {
  available: boolean;
  module: string;
  mode: 'independent-owner' | 'owner-mobile-library' | 'sanitized-snapshot';
  message: string;
  features: string[];
  counts?: { works: number; media: number; transcripts: number; comments: number; analyses: number };
  chat_enabled?: boolean;
  doubao_asr_enabled?: boolean;
}

export interface ModelMrWork {
  id: number;
  title: string;
  description: string;
  url: string;
  published_at: string;
  has_video_text: boolean;
  has_interpretation: boolean;
  comment_count: number;
  media_available: boolean;
  video_url: string;
  keywords: string[];
}

export interface ModelMrTranscript {
  text: string;
  source: string;
  language: string;
  created_at: string;
}

export interface ModelMrComment {
  id: number;
  author: string;
  text: string;
  like_count: number;
  reply_count: number;
  published_at: string;
  kind: string;
  reply_depth: number;
  thread_key: string;
  author_liked: boolean;
}

export interface ModelMrStockMentionItem {
  rank: number;
  name: string;
  code: string;
  comment_count: number;
  mention_count: number;
  fan_comment_count: number;
  author_comment_count: number;
  examples: string[];
  comment_ids: number[];
}

export interface ModelMrStockMentionReport {
  total_comments: number;
  stock_count: number;
  items: ModelMrStockMentionItem[];
  uncertain: Array<{ text: string; comment_count: number; candidates: string[] }>;
  method: string;
  api_used: boolean;
  message: string;
}

export interface ModelMrWorkDetail {
  version: number;
  work: ModelMrWork;
  video_text: { text: string; official: boolean; source: string; updated_at: string };
  interpretation: { text: string; updated_at: string };
  transcripts: ModelMrTranscript[];
  comments: ModelMrComment[];
  stock_mentions: ModelMrStockMentionReport;
  comment_total: number;
  capabilities: {
    video: boolean;
    save_title: boolean;
    save_video_text: boolean;
    transcribe_video: boolean;
    doubao_asr: boolean;
    comments: boolean;
  };
}

export interface ModelMrTranscriptionResult {
  text: string;
  engine: string;
  cached: boolean;
  message: string;
}

export interface ModelMrThoughtCategory {
  id: number;
  name: string;
  description: string;
  level: number;
  parent_id: number | null;
  video_count: number;
}

export interface ModelMrChatConfig {
  enabled: boolean;
  default_model: string;
  models: Array<{ id: string; label: string; description: string }>;
  message: string;
}

export interface ModelMrChatResult {
  answer: string;
  model: string;
  response_id?: string | null;
  tools_used?: string[];
}

export interface TranslationStatus {
  enabled: boolean;
  provider: string;
  provider_label: string;
  external: boolean;
  cached_titles: number;
  used_characters_today: number;
  daily_character_limit: number | null;
  remaining_characters_today: number | null;
  official_public_limit: number | null;
  target_language: string;
}

export interface TranslationBatchResult {
  ok: boolean;
  translations: Record<string, string>;
  providers: Record<string, string>;
  translated_count: number;
  cached_count: number;
  skipped_count: number;
  pending_count: number;
  quota_exhausted: boolean;
  errors: string[];
  status: TranslationStatus;
}

export interface ReaderTranslationResult {
  ok: boolean;
  item_id: number;
  source_url?: string;
  source_kind?: 'article_excerpt' | 'summary';
  original_excerpt?: string;
  translated_text?: string;
  provider?: string;
  source_truncated?: boolean;
  translation_partial?: boolean;
  cached?: boolean;
  updated_at?: string;
  quota_exhausted?: boolean;
  errors?: string[];
  error?: string;
  fetch_error?: string;
  status?: TranslationStatus;
}

export interface SectionDefinition {
  id: string;
  title: string;
  subtitle: string;
  topic?: string;
  accent: string;
}

export interface WatchEventSource {
  name: string;
  url: string;
  verifiedAt: string;
}

export interface WatchEventMonitoring {
  contractVersion: number;
  coverage: 'verified' | 'unverified';
  verifiedAt: string;
  publisher: { name: string; url: string };
  release: {
    timeStatus: 'confirmed' | 'date-only' | 'tentative' | 'unverified';
    scheduledAt: string | null;
    timeZone: 'Asia/Shanghai';
    label: string;
    windowStart: string;
    windowEnd: string;
  };
  channels: Array<{
    key: string;
    publisher: string;
    name: string;
    url: string;
    type: string;
    role: string;
    verifiedAt: string;
    expectedTerms: string[];
  }>;
  expectedTerms: string[];
}

export interface WatchEventOfficialChannel {
  channel_key: string;
  publisher: string;
  name: string;
  channel_type: string;
  channel_role: string;
  url: string;
  verified_at: string;
  time_status: string;
  window_start: string;
  window_end: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_changed_at: string | null;
  next_check_at: string | null;
  http_status: number | null;
  signal_found: boolean;
  last_error: string | null;
}

export interface WatchEventMatch {
  item_id: number;
  title: string;
  translated_title: string | null;
  url: string;
  published_at: string | null;
  first_seen_at: string;
  importance_score: number;
  match_score: number;
  matched_terms: string[];
  matched_at: string;
}

export interface WatchEventAnalysisFeedback {
  status: 'received' | 'analyzing' | 'retrying' | 'published' | 'skipped' | 'failed';
  message: string;
  attemptCount: number;
  maxAttempts: number;
  updatedAt: string;
}

export interface WatchEvent {
  event_key: string;
  scope: 'home' | 'zijin';
  source_kind: 'timeline' | 'manual' | 'research' | string;
  source_event_id: string;
  title: string;
  event_date: string;
  event_time: string;
  category: string;
  importance: number;
  event_status: string;
  note: string;
  sources: WatchEventSource[];
  monitoring: WatchEventMonitoring;
  official_channels: WatchEventOfficialChannel[];
  source_updated_at: string;
  last_synced_at: string;
  last_checked_at: string | null;
  match_count: number;
  latest_match_at: string | null;
  monitor_status: string;
  official_status: 'changed' | 'error' | 'reachable' | 'pending' | 'unconfigured';
  candidate_status: string;
  pipeline_status: string;
  analysis_feedback: Partial<WatchEventAnalysisFeedback>;
  latest_signal: {
    signal_id: string;
    detected_at: string;
    status: 'pending' | 'delivered' | 'failed';
    delivery_attempts: number;
    delivered_at: string | null;
    compass_signal_id: string;
    compass_signal_status: string;
    last_error: string | null;
  } | null;
  latest_matches: WatchEventMatch[];
}

export interface WatchEventsResponse {
  events: WatchEvent[];
  counts: {
    total: number;
    home: number;
    zijin: number;
    matched: number;
    configured: number;
    official_reachable: number;
    official_changed: number;
    official_errors: number;
    signals_detected: number;
    signals_delivered: number;
  };
  sync: {
    source_url: string;
    last_attempt_at: string | null;
    last_success_at: string | null;
    last_error: string | null;
    source_revision: number | null;
    event_count: number;
  } | null;
  time_zone: 'Asia/Shanghai';
}
