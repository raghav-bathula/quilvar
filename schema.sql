-- Quilvar Prediction Market Agent — Supabase Schema
-- Run once in Supabase SQL Editor: https://supabase.com/dashboard/project/_/sql

CREATE TABLE alerts (
  id              bigserial PRIMARY KEY,
  scan_time       timestamptz NOT NULL,
  source          text,
  tier            int,
  title           text,
  link            text,
  themes          text[],
  watchlist       text[],
  tickers         text[],      -- also used for crypto assets
  score           int,
  shadow_mode     boolean DEFAULT true,
  gap_flag        boolean,
  market          text,
  platform        text,
  market_url      text,
  market_prob     float,       -- Polymarket yes-price at scan time (0.0–1.0)
  market_question text,        -- Haiku-generated prediction market question
  surprise        int,         -- 0-10, how unexpected vs consensus
  rationale       text,        -- Haiku one-line explanation
  outcome         text,
  outcome_1d      text,
  outcome_7d      text,
  outcome_30d     text,
  validated_1d    boolean,
  validated_7d    boolean,
  validated_30d   boolean,
  price_data_1d   jsonb,
  price_data_7d   jsonb,
  price_data_30d  jsonb,
  reasoned_at     timestamptz,
  ensemble_prob   float,
  expert_avg_prob float,
  gap             float,
  gap_strength    text,
  should_alert    boolean,
  reasoning_json  jsonb,       -- stores asset_class, direction for crypto alerts
  created_at      timestamptz DEFAULT now()
);

CREATE TABLE signal_config (
  key        text PRIMARY KEY,
  value      jsonb,
  updated_at timestamptz DEFAULT now(),
  notes      text
);

CREATE INDEX idx_scan_time   ON alerts(scan_time);
CREATE INDEX idx_score       ON alerts(score);
CREATE INDEX idx_outcome_1d  ON alerts(outcome_1d);
CREATE INDEX idx_reasoned_at ON alerts(reasoned_at);
CREATE INDEX idx_gap         ON alerts(gap);

-- Article deduplication — 7-day TTL, replaces signal_config JSON blob approach
CREATE TABLE seen_urls (
  url_hash  text        PRIMARY KEY,
  stream    text        NOT NULL,       -- 'equity' or 'crypto'
  seen_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_seen_urls_stream_seen_at ON seen_urls (stream, seen_at DESC);

-- Weak signals — score 1-3 articles stored for pattern analysis (30-day TTL)
-- Separate from alerts table to protect calibration integrity
CREATE TABLE weak_signals (
  id         bigserial PRIMARY KEY,
  scan_time  timestamptz NOT NULL,
  source     text,
  tier       int,
  title      text,
  link       text,
  score      int,
  themes     text[],
  tickers    text[],
  rationale  text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX idx_weak_signals_scan_time ON weak_signals(scan_time);
CREATE INDEX idx_weak_signals_tickers   ON weak_signals USING gin(tickers);
CREATE INDEX idx_weak_signals_themes    ON weak_signals USING gin(themes);
