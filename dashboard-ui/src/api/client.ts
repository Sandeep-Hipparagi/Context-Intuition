/**
 * VibeCore Live WebSocket & REST Client.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 */

import { ActionEnvelope, ApprovalStatus, BehavioralRule, ContextNode, MorningBrief, ContextEvent, TraceSpan, TraceSummary } from '../types/schema';

export const API_BASE = 'http://127.0.0.1:8000';
export const WS_BASE = 'ws://127.0.0.1:8000';

export function getStoredToken(): string {
  return localStorage.getItem('vibecore_bridge_token') || '';
}

export function setStoredToken(token: string): void {
  localStorage.setItem('vibecore_bridge_token', token.trim());
}

// ============================================================================
// REST API Helpers
// ============================================================================

export async function fetchAuthToken(): Promise<{ token: string; status: string }> {
  const res = await fetch(`${API_BASE}/api/auth/token`);
  if (!res.ok) throw new Error('Failed to retrieve active bridge token');
  return res.json();
}

export async function fetchPendingApprovals(): Promise<ActionEnvelope[]> {
  const res = await fetch(`${API_BASE}/api/approvals`);
  if (!res.ok) throw new Error('Failed to fetch pending approvals');
  return res.json();
}

export async function resolveApprovalAction(
  actionId: string,
  status: ApprovalStatus,
  signature: string = 'sig_user_approved',
  editedPayload?: Record<string, unknown>,
  feedback?: string,
): Promise<ActionEnvelope> {
  const res = await fetch(`${API_BASE}/api/approvals/${actionId}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      status,
      user_signature: signature,
      edited_payload: editedPayload,
      user_feedback: feedback,
    }),
  });
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.detail || `Resolution failed with status ${res.status}`);
  }
  return res.json();
}

export async function fetchLatestMorningBrief(): Promise<MorningBrief> {
  const res = await fetch(`${API_BASE}/api/brief/latest`);
  if (!res.ok) throw new Error('Failed to fetch latest morning brief');
  return res.json();
}

export async function triggerMorningBrief(): Promise<MorningBrief> {
  const res = await fetch(`${API_BASE}/api/brief/trigger`, { method: 'POST' });
  if (!res.ok) throw new Error('Failed to trigger morning brief');
  return res.json();
}

export async function fetchContextNodes(nodeType?: string, tag?: string): Promise<ContextNode[]> {
  const params = new URLSearchParams();
  if (nodeType) params.append('node_type', nodeType);
  if (tag) params.append('tag', tag);

  const res = await fetch(`${API_BASE}/api/context/nodes?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to fetch context nodes');
  return res.json();
}

export async function fetchBehavioralRules(): Promise<BehavioralRule[]> {
  const res = await fetch(`${API_BASE}/api/context/rules`);
  if (!res.ok) throw new Error('Failed to fetch behavioral rules');
  return res.json();
}

export interface HealthResponse {
  status: string;
  timestamp: string;
  version: string;
  scheduler_running: boolean;
  browser_bridges_connected: number;
  dashboard_clients_connected: number;
  profile: string;
}

export async function fetchHealth(): Promise<HealthResponse | null> {
  const res = await fetch(`${API_BASE}/api/health`);
  if (!res.ok) return null;
  return res.json();
}

// ============================================================================
// Context Graph History & Replay
// ============================================================================

export async function fetchNodeHistory(nodeId: string): Promise<ContextEvent[]> {
  const res = await fetch(`${API_BASE}/api/context/nodes/${nodeId}/history`);
  if (!res.ok) throw new Error('Failed to fetch node history');
  return res.json();
}

export async function replayContextGraph(asOf: string): Promise<ContextNode[]> {
  const res = await fetch(`${API_BASE}/api/context/replay?as_of=${encodeURIComponent(asOf)}`);
  if (!res.ok) throw new Error('Failed to replay context graph');
  return res.json();
}

export interface SearchResult {
  node: ContextNode;
  score: number;
}

export async function searchContext(
  q: string,
  limit: number = 10,
  semanticWeight: number = 0.6,
  nodeType?: string,
  tag?: string
): Promise<SearchResult[]> {
  const params = new URLSearchParams();
  params.append('q', q);
  params.append('limit', limit.toString());
  params.append('semantic_weight', semanticWeight.toString());
  if (nodeType) params.append('node_type', nodeType);
  if (tag) params.append('tag', tag);

  const res = await fetch(`${API_BASE}/api/context/search?${params.toString()}`);
  if (!res.ok) throw new Error('Failed to search context');
  return res.json();
}

// ============================================================================
// Distributed Tracing
// ============================================================================

export async function fetchRecentTraces(limit: number = 100): Promise<TraceSummary[]> {
  const res = await fetch(`${API_BASE}/api/traces/recent?limit=${limit}`);
  if (!res.ok) throw new Error('Failed to fetch recent traces');
  return res.json();
}

export async function fetchTrace(traceId: string): Promise<TraceSpan[]> {
  const res = await fetch(`${API_BASE}/api/traces/${traceId}`);
  if (!res.ok) throw new Error('Failed to fetch trace');
  return res.json();
}

// ============================================================================
// Real-Time WebSocket Telemetry Client
// ============================================================================

export type WsConnectionState = 'CONNECTED' | 'DISCONNECTED' | 'AUTH_ERROR';

export class DashboardWebSocketClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private reconnectAttempts = 0;
  private listeners: ((event: string, data: any) => void)[] = [];
  private stateListeners: ((state: WsConnectionState) => void)[] = [];
  public state: WsConnectionState = 'DISCONNECTED';

  public connect(): void {
    if (this.socket && (this.socket.readyState === WebSocket.OPEN || this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const token = getStoredToken();
    const url = token ? `${WS_BASE}/ws/dashboard?token=${encodeURIComponent(token)}` : `${WS_BASE}/ws/dashboard`;

    try {
      this.socket = new WebSocket(url);
    } catch (e) {
      this.setState('DISCONNECTED');
      this.scheduleReconnect();
      return;
    }

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.setState('CONNECTED');
    };

    this.socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        for (const listener of this.listeners) {
          listener(payload.event, payload.data);
        }
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    };

    this.socket.onclose = (event) => {
      this.setState(event.code === 1008 ? 'AUTH_ERROR' : 'DISCONNECTED');
      this.scheduleReconnect();
    };

    this.socket.onerror = () => {
      this.socket?.close();
    };
  }

  public disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.setState('DISCONNECTED');
  }

  public subscribe(listener: (event: string, data: any) => void): () => void {
    this.listeners.push(listener);
    return () => {
      this.listeners = this.listeners.filter((l) => l !== listener);
    };
  }

  public subscribeState(listener: (state: WsConnectionState) => void): () => void {
    this.stateListeners.push(listener);
    listener(this.state);
    return () => {
      this.stateListeners = this.stateListeners.filter((l) => l !== listener);
    };
  }

  private setState(state: WsConnectionState): void {
    this.state = state;
    for (const l of this.stateListeners) {
      l(state);
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    const backoff = Math.min(30000, 1000 * Math.pow(1.5, this.reconnectAttempts));
    this.reconnectAttempts++;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, backoff);
  }
}

export const wsClient = new DashboardWebSocketClient();
