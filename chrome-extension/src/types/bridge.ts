/**
 * TypeScript Data Contracts for VibeCore Chrome Extension Bridge.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 */

export type TierLevel = 'TIER_0_OBSERVE' | 'TIER_1_STAGE' | 'TIER_2_COMMIT';

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EDITED' | 'EXPIRED';

export type BridgeMessageType =
  | 'HANDSHAKE'
  | 'HEARTBEAT'
  | 'PAGE_STATE_CHANGED'
  | 'DOM_MUTATION_OBSERVED'
  | 'EXECUTE_COMMAND'
  | 'COMMAND_RESULT'
  | 'COMMIT_ACTION'
  | 'DOM_GROCERY_SNAPSHOT'
  | 'WHATSAPP_INCOMING_MESSAGE'
  | 'INSTAGRAM_SAVED_BATCH'
  | 'SELECTOR_CANARY_WARNING'
  | 'ERROR';

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

export interface BridgeMessageMetadata {
  trace_id?: string;
  span_id?: string;
}

export interface BridgeMessage {
  message_id: string;
  msg_type: BridgeMessageType;
  tab_id?: number;
  url?: string;
  payload: Record<string, unknown>;
  timestamp: string;
  metadata?: BridgeMessageMetadata;
}

export interface GroceryProductSnapshot {
  portal: string;
  title: string;
  unit_price: number | null;
  currency: string;
  in_stock: boolean;
  sku?: string;
  url: string;
  can_stage_cart: boolean;
  extracted_at: string;
}

export interface WhatsAppMessageSnapshot {
  app: 'whatsapp_web';
  chat_name: string;
  sender: string;
  text: string;
  timestamp: string;
  is_group?: boolean;
}

export interface InstagramSavedItem {
  post_url: string;
  author: string;
  caption: string;
  alt_text?: string;
  extracted_at: string;
}

export interface WorkflowToggles {
  whatsapp_copilot: boolean;
  grocery_tracker: boolean;
  instagram_archivist: boolean;
}

export interface CanaryWarningPayload {
  pluginId: string;
  pluginName: string;
  missingSelectors: string[];
  url: string;
  timestamp: string;
  trace_id: string;
}

/**
 * Generate a 32-hex-char W3C-compatible trace_id
 */
export function generateTraceId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Generate a 16-hex-char W3C-compatible span_id
 */
export function generateSpanId(): string {
  const bytes = new Uint8Array(8);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Format W3C traceparent header: version-trace-id-parent-id-flags
 */
export function formatTraceparent(traceId: string, spanId: string, flags: string = '01'): string {
  return `00-${traceId}-${spanId}-${flags}`;
}

/**
 * Parse W3C traceparent header
 */
export function parseTraceparent(header: string): { traceId: string; parentSpanId: string; flags: string } | null {
  if (!header) return null;
  const parts = header.split('-');
  if (parts.length !== 4) return null;
  const [version, traceId, parentId, flags] = parts;
  if (version !== '00' || traceId.length !== 32 || parentId.length !== 16) return null;
  return { traceId, parentSpanId: parentId, flags };
}
