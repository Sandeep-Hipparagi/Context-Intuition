/**
 * TypeScript Data Contracts matching core-orchestrator/app/schemas.py
 * For Dashboard UI and Generative UI widgets.
 */

export type TierLevel = 'TIER_0_OBSERVE' | 'TIER_1_STAGE' | 'TIER_2_COMMIT';

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EDITED' | 'EXPIRED';

export type ContextNodeType = 
  | 'FACT'
  | 'PREFERENCE'
  | 'EPISODE'
  | 'GOAL'
  | 'ENTITY'
  | 'TRAJECTORY'
  | 'DECISION_HISTORY';

export type ContextEventType =
  | 'NODE_CREATED'
  | 'NODE_UPDATED'
  | 'CONFIDENCE_REINFORCED'
  | 'DECAY_APPLIED'
  | 'CONSOLIDATED_INTO_TRAJECTORY'
  | 'NODE_PRUNED';

export interface ContextNode {
  node_id: string;
  node_type: ContextNodeType;
  title: string;
  content: Record<string, unknown>;
  confidence: number;
  decay_rate: number;
  access_count: number;
  last_accessed: string;
  created_at: string;
  updated_at: string;
  source: string;
  tags: string[];
}

export interface ContextEvent {
  event_id: string;
  node_id: string;
  event_type: ContextEventType;
  delta_payload: Record<string, unknown>;
  agent_id?: string;
  trace_id?: string;
  created_at: string;
}

export interface BehavioralRule {
  rule_id: string;
  category: string;
  trigger_pattern: string;
  action_directive: string;
  original_draft_sample?: string;
  user_revised_sample?: string;
  inferred_delta_rationale: string;
  weight: number;
  reinforcement_count: number;
  created_at: string;
  last_triggered_at?: string;
}

export interface DiffPreview {
  original?: string;
  proposed?: string;
  structured_diff?: Record<string, unknown>;
  affected_resource?: string;
}

export interface ActionEnvelope {
  action_id: string;
  agent_id: string;
  session_id: string;
  tool_name: string;
  tier: TierLevel;
  risk_score: number;
  risk_factors: string[];
  payload: Record<string, unknown>;
  diff_preview?: DiffPreview;
  status: ApprovalStatus;
  created_at: string;
  resolved_at?: string;
  user_signature?: string;
  user_feedback?: string;
  execution_result?: Record<string, unknown>;
  trace_id?: string;
  span_id?: string;
}

export interface TraceSpan {
  trace_id: string;
  span_id: string;
  parent_span_id?: string;
  component: string;
  operation: string;
  status: string;
  duration_ms?: number;
  attributes: Record<string, unknown>;
  started_at: string;
}

export interface TraceSummary {
  trace_id: string;
  started_at: string;
  span_count: number;
  max_duration_ms?: number;
  components: string[];
}

export interface CommitmentItem {
  item_id: string;
  source: 'gmail' | 'calendar' | 'whatsapp' | string;
  summary: string;
  counterparty: string;
  due_datetime?: string;
  confidence: number;
  staged_reply_action_id?: string;
}

export interface ScheduleConflict {
  event_a_title: string;
  event_b_title: string;
  start_time: string;
  end_time: string;
  recommended_resolution: string;
}

export interface TechRadarItem {
  title: string;
  url: string;
  source: 'arxiv' | 'github';
  relevance_rationale: string;
  trajectory_tag: string;
}

export interface MorningBrief {
  brief_id: string;
  generated_at: string;
  window_hours: number;
  executive_summary: string;
  commitments_detected: CommitmentItem[];
  schedule_conflicts: ScheduleConflict[];
  pending_approvals: ActionEnvelope[];
  tech_radar: TechRadarItem[];
  subscription_alerts: string[];
}

export interface CanaryWarningPayload {
  pluginId: string;
  pluginName: string;
  missingSelectors: string[];
  url: string;
  timestamp: string;
  trace_id: string;
}
