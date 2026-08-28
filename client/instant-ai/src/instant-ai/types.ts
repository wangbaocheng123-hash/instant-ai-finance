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
