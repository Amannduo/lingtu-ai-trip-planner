// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface MapContextPOI {
  name: string
  category: '餐饮' | '商店' | '周边景点' | '交通' | string
  address: string
  location: Location
  poi_id?: string
  source?: string
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  photos?: string[]
  poi_id?: string
  coordinate_source?: string
  ticket_price?: number
  ticket_price_status?: 'unknown' | 'verified' | 'free'
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
  poi_id?: string
  coordinate_source?: string
}

export interface RouteSegment {
  from_name: string
  to_name: string
  origin_address: string
  destination_address: string
  route_type: 'walking' | 'driving' | 'transit' | string
  distance: number
  duration: number
  description: string
  path?: Location[]
  source?: string
  verified?: boolean
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
  poi_id?: string
  selection_reason?: string
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
  known_total: number
  pending_ticket_items: string[]
  hotel_nights: number
  hotel_rooms: number
  hotel_unit_price: number
  intercity_transportation: number
  local_transportation: number
  transport_unit_price: number
  budget_source: string
  hotel_reference?: string | null
  transport_reference?: string | null
  budget_notes: string[]
}

export interface WebReference {
  title: string
  url: string
  site_name: string
  source_type: string
  publish_time?: number | null
}

export interface AgentAuditResult {
  status: 'passed' | 'warning' | 'failed' | string
  source: string
  checked_items: string[]
  issues: string[]
  suggestions: string[]
  audit_level?: 'format_only' | 'semantic_verified' | 'offline_fallback' | string
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  routes: RouteSegment[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

export interface TripPlanQualityIssue {
  code: string
  severity: 'info' | 'warning' | 'error' | string
  path?: string
  message: string
  suggestion?: string
  auto_repaired?: boolean
}

export interface TripPlanQualityResult {
  status: 'passed' | 'warning' | 'failed' | 'review' | string
  score: number
  publishable: boolean
  review_required?: boolean
  /** blocked | needs_review | publishable — unified gate decision */
  quality_status?: 'blocked' | 'needs_review' | 'publishable' | string
  /** full | legacy_weak — validation context completeness on edits */
  validation_mode?: 'full' | 'legacy_weak' | string
  checked_items?: string[]
  issues?: TripPlanQualityIssue[]
  verified_facts?: number
  generated_at?: string
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  generation_mode: 'primary' | 'repaired' | 'map_fallback'
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
  web_guide?: string | null
  web_references: WebReference[]
  agent_audit?: AgentAuditResult | null
  quality?: TripPlanQualityResult | null
  map_context?: MapContextPOI[]
}

export interface TripFormData {
  origin_city?: string | null
  city: string
  destination_source?: 'manual' | 'recommendation'
  start_date: string
  end_date: string
  travel_days: number
  travelers: number
  budget?: number | null
  transportation: string
  intercity_transportation?: string | null
  accommodation: string
  preferences: string[]
  free_text_input: string
  email_on_completion?: boolean
  delivery_email?: string | null
  /** Optional client snapshot; server rebuilds authoritative contract */
  semantic_contract?: SemanticTripContract | null
  /** Set true after frontend secondary confirm of pending/conflict risks */
  semantic_risks_acknowledged?: boolean
  date_pattern?: 'weekend' | 'explicit' | 'unknown' | null
  weekend_style?: 'sat_sun' | 'fri_sun_optional' | null
  early_arrival_hint?: string | null
  departure_mode?: 'morning_first_day' | 'evening_before' | null
  /** Server-signed session contract token, returned verbatim */
  recommendation_token?: string | null
}

export interface EmailDeliveryResult {
  requested: boolean
  sent: boolean
  dry_run: boolean
  blocked: boolean
  to?: string | null
  message: string
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
  plan_no?: string | null
  email_delivery?: EmailDeliveryResult | null
  /** Unified gate decision — authoritative over inferring from quality */
  quality_status?: 'blocked' | 'needs_review' | 'publishable' | ''
  /** No blocking issues but score below threshold; not auto-persisted */
  needs_review?: boolean
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface RecommendationContext {
  origin_city?: string | null
  budget?: number | null
  travel_days?: number | null
  travelers?: number | null
  start_date?: string | null
  end_date?: string | null
  recommendation_count?: number
  preferences: string[]
  transportation?: string | null
  accommodation?: string | null
}

export interface DestinationChatRequest {
  messages: ChatMessage[]
  context: RecommendationContext
}

export interface RecommendationFormPatch {
  city: string
  destination_source?: 'manual' | 'recommendation'
  origin_city?: string | null
  start_date?: string | null
  end_date?: string | null
  travel_days?: number | null
  travelers?: number | null
  budget?: number | null
  transportation?: string | null
  accommodation?: string | null
  preferences: string[]
  free_text_input: string
  date_pattern?: 'weekend' | 'explicit' | 'unknown' | null
  weekend_style?: 'sat_sun' | 'fri_sun_optional' | null
  early_arrival_hint?: string | null
  departure_mode?: 'morning_first_day' | 'evening_before' | null
  schedule_option?: 'default_weekend' | 'friday_early' | null
}

export interface DestinationRecommendation {
  city: string
  reason: string
  decision_label: string
  tradeoff: string
  suggested_days: number
  estimated_budget?: number | null
  pace: string
  budget_fit: string
  origin_note?: string | null
  highlights: string[]
  weather_summary?: string | null
  suggested_preferences: string[]
  form_patch: RecommendationFormPatch
  date_pattern?: 'weekend' | 'explicit' | 'unknown' | null
  weekend_style?: 'sat_sun' | 'fri_sun_optional' | null
  early_arrival_hint?: string | null
  departure_mode?: 'morning_first_day' | 'evening_before' | null
  schedule_option?: 'default_weekend' | 'friday_early' | null
  schedule_summary?: string | null
}

export type FieldSource =
  | 'user_explicit'
  | 'rule_inferred'
  | 'form_confirmed'
  | 'system_default'
  | 'unknown'

export interface FieldBinding {
  value?: unknown
  source: FieldSource
  confidence: 'high' | 'medium' | 'low'
  pending_confirmation: boolean
  evidence?: string
  conflicts?: string[]
}

export interface SemanticTripContract {
  raw_text?: string
  origin_city?: FieldBinding
  destination_city?: FieldBinding
  start_date?: FieldBinding
  end_date?: FieldBinding
  travel_days?: FieldBinding
  travelers?: FieldBinding
  travel_party?: FieldBinding
  budget?: FieldBinding
  pace?: FieldBinding
  preferences?: FieldBinding
  transportation?: FieldBinding
  accommodation?: FieldBinding
  date_pattern?: FieldBinding
  weekend_style?: FieldBinding
  early_arrival_hint?: FieldBinding
  departure_mode?: FieldBinding
  conflicts?: string[]
  pending_fields?: string[]
}

export interface DestinationChatResponse {
  success: boolean
  message: string
  reply: string
  needs_more_info: boolean
  /** Apply-safe flat fields plus optional semantic_contract / conflicts / pending_fields */
  interpreted_context: Record<string, unknown>
  semantic_contract?: SemanticTripContract | null
  /** Server-signed session contract token for the generation request */
  contract_token?: string | null
  recommendations: DestinationRecommendation[]
}

export interface AgentChatRequest {
  message: string
  email?: string | null
}

export interface AgentPermission {
  role: string
  allowed: boolean
  reason: string
}

export interface AgentChatResponse {
  success: boolean
  intent: string
  agent: string
  tool: string
  table: Record<string, any>[]
  chart?: Record<string, any> | null
  result: string
  permission: AgentPermission
  sensitive: Record<string, any>
  extra: Record<string, any>
}
