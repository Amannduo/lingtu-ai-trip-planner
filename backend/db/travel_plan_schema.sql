CREATE TABLE IF NOT EXISTS travel_plans (
  id BIGSERIAL PRIMARY KEY,
  plan_no VARCHAR(64) UNIQUE NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  user_role VARCHAR(32) NOT NULL DEFAULT 'user',
  origin_city VARCHAR(64),
  destination_city VARCHAR(64) NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  travel_days INTEGER NOT NULL CHECK (travel_days > 0),
  travelers INTEGER NOT NULL DEFAULT 1 CHECK (travelers > 0),
  budget NUMERIC(12,2),
  actual_cost NUMERIC(12,2),
  transportation VARCHAR(64),
  intercity_transportation VARCHAR(64),
  accommodation VARCHAR(64),
  preferences JSONB NOT NULL DEFAULT '[]',
  free_text_input TEXT,
  generated_summary TEXT,
  contact_name VARCHAR(64),
  contact_phone VARCHAR(32),
  contact_email VARCHAR(128),
  source VARCHAR(32) NOT NULL DEFAULT 'mock',
  status VARCHAR(32) NOT NULL DEFAULT 'completed',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_travel_plans_user_id ON travel_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_travel_plans_user_role ON travel_plans(user_role);
CREATE INDEX IF NOT EXISTS idx_travel_plans_destination_city ON travel_plans(destination_city);
CREATE INDEX IF NOT EXISTS idx_travel_plans_start_date ON travel_plans(start_date);
CREATE INDEX IF NOT EXISTS idx_travel_plans_created_at ON travel_plans(created_at);
CREATE INDEX IF NOT EXISTS idx_travel_plans_preferences ON travel_plans USING GIN(preferences);

CREATE TABLE IF NOT EXISTS travel_plan_tags (
  id BIGSERIAL PRIMARY KEY,
  plan_id BIGINT NOT NULL REFERENCES travel_plans(id) ON DELETE CASCADE,
  user_id VARCHAR(64) NOT NULL,
  tag_name VARCHAR(64) NOT NULL,
  tag_score NUMERIC(5,4) NOT NULL DEFAULT 0,
  source VARCHAR(32) NOT NULL DEFAULT 'seed_rule',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_travel_plan_tags_plan_id ON travel_plan_tags(plan_id);
CREATE INDEX IF NOT EXISTS idx_travel_plan_tags_user_id ON travel_plan_tags(user_id);
CREATE INDEX IF NOT EXISTS idx_travel_plan_tags_tag_name ON travel_plan_tags(tag_name);

CREATE TABLE IF NOT EXISTS user_interest_profiles (
  user_id VARCHAR(64) PRIMARY KEY,
  plan_count INTEGER NOT NULL DEFAULT 0,
  top_tags JSONB NOT NULL DEFAULT '[]',
  favorite_cities JSONB NOT NULL DEFAULT '[]',
  avg_budget NUMERIC(12,2),
  avg_actual_cost NUMERIC(12,2),
  avg_travel_days NUMERIC(6,2),
  traveler_type VARCHAR(64),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS recommendation_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  request_text TEXT NOT NULL,
  recommended_cities JSONB NOT NULL DEFAULT '[]',
  matched_tags JSONB NOT NULL DEFAULT '[]',
  reason TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_audit_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(64),
  user_role VARCHAR(32),
  message TEXT,
  routed_agent VARCHAR(64),
  tool_name VARCHAR(64),
  permission_allowed BOOLEAN NOT NULL DEFAULT TRUE,
  sensitive_hit BOOLEAN NOT NULL DEFAULT FALSE,
  audit_detail JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_user_id ON agent_audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_audit_logs_created_at ON agent_audit_logs(created_at);

CREATE TABLE IF NOT EXISTS user_query_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(64),
  user_role VARCHAR(32),
  question TEXT NOT NULL,
  intent VARCHAR(64),
  sql_text TEXT,
  result_summary TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_query_logs_user_id ON user_query_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_user_query_logs_created_at ON user_query_logs(created_at);
