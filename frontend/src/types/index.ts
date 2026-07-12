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
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
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

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
  web_guide?: string | null
  web_references: WebReference[]
  agent_audit?: AgentAuditResult | null
  map_context?: MapContextPOI[]
}

export interface TripFormData {
  origin_city?: string | null
  city: string
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
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
  plan_no?: string | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface RecommendationContext {
  origin_city?: string | null
  budget?: number | null
  travel_days?: number | null
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
  travel_days?: number | null
  budget?: number | null
  transportation?: string | null
  accommodation?: string | null
  preferences: string[]
  free_text_input: string
}

export interface DestinationRecommendation {
  city: string
  reason: string
  suggested_days: number
  budget_fit: string
  origin_note?: string | null
  highlights: string[]
  weather_summary?: string | null
  suggested_preferences: string[]
  form_patch: RecommendationFormPatch
}

export interface DestinationChatResponse {
  success: boolean
  message: string
  reply: string
  needs_more_info: boolean
  recommendations: DestinationRecommendation[]
}

export interface AgentChatRequest {
  user_id: string
  role: 'guest' | 'user' | 'manager' | 'admin'
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
