/**
 * Manifest V3 Background Service Worker for VibeCore.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 * Maintains persistent bidirectional WebSocket bridge to local Python orchestrator.
 * Includes IndexedDB offline write-ahead queue for telemetry durability.
 */

import { BridgeMessage, BridgeMessageType } from '../types/bridge';
import {
  enqueueFrame,
  getAllFrames,
  deleteFrame,
  updateFrameRetry,
  getQueueSize,
} from './offline_queue';

const WS_BASE_URL = 'ws://127.0.0.1:8000/ws/browser-bridge';
let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let heartbeatTimer: number | null = null;
let flushTimer: number | null = null;
let reconnectAttempts = 0;
let connectionStatus: 'CONNECTED' | 'DISCONNECTED' | 'AUTH_ERROR' = 'DISCONNECTED';
let isFlushing = false;

console.log('[VibeCore ServiceWorker] Initializing background bridge...');

async function getWsUrl(): Promise<string> {
  try {
    const data = await chrome.storage.local.get(['vibecore_token']);
    if (data.vibecore_token) {
      return `${WS_BASE_URL}?token=${encodeURIComponent(data.vibecore_token)}`;
    }
  } catch (e) {
    // Fallback if storage unavailable
  }
  return WS_BASE_URL;
}

async function connectWebSocket(): Promise<void> {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const url = await getWsUrl();
  console.log(`[VibeCore ServiceWorker] Connecting to ${url}...`);

  try {
    socket = new WebSocket(url);
  } catch (e) {
    connectionStatus = 'DISCONNECTED';
    scheduleReconnect();
    return;
  }

  socket.onopen = () => {
    console.log('[VibeCore ServiceWorker] Connected to local Python orchestrator.');
    connectionStatus = 'CONNECTED';
    reconnectAttempts = 0;

    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'HANDSHAKE',
      payload: {
        agent: 'vibecore-chrome-bridge',
        version: '0.1.0',
        userAgent: navigator.userAgent
      },
      timestamp: new Date().toISOString()
    });

    startHeartbeat();
    startFlushTimer();
    flushOfflineQueue().catch(console.error);
  };

  socket.onmessage = async (event: MessageEvent) => {
    try {
      const message: BridgeMessage = JSON.parse(event.data);
      console.log('[VibeCore ServiceWorker] Received RPC from backend:', message.msg_type);

      if (message.msg_type === 'EXECUTE_COMMAND') {
        await handleExecuteCommand(message);
      } else if (message.msg_type === 'COMMIT_ACTION') {
        await handleCommitAction(message);
      }
    } catch (err) {
      console.error('[VibeCore ServiceWorker] Error parsing message:', err);
    }
  };

  socket.onclose = (event) => {
    console.warn('[VibeCore ServiceWorker] WebSocket connection closed. Code:', event.code);
    connectionStatus = event.code === 1008 ? 'AUTH_ERROR' : 'DISCONNECTED';
    stopHeartbeat();
    stopFlushTimer();
    scheduleReconnect();
  };

  socket.onerror = (err) => {
    console.error('[VibeCore ServiceWorker] WebSocket error:', err);
    socket?.close();
  };
}

function scheduleReconnect(): void {
  if (reconnectTimer) return;
  const backoff = Math.min(30000, 1000 * Math.pow(1.5, reconnectAttempts));
  reconnectAttempts++;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWebSocket().catch(console.error);
  }, backoff) as unknown as number;
}

function startHeartbeat(): void {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      sendToOrchestrator({
        message_id: crypto.randomUUID(),
        msg_type: 'HEARTBEAT',
        payload: { status: 'alive' },
        timestamp: new Date().toISOString()
      });
    }
  }, 15000) as unknown as number;
}

function stopHeartbeat(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function startFlushTimer(): void {
  stopFlushTimer();
  flushTimer = setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN && !isFlushing) {
      flushOfflineQueue().catch(console.error);
    }
  }, 5000) as unknown as number;
}

function stopFlushTimer(): void {
  if (flushTimer) {
    clearInterval(flushTimer);
    flushTimer = null;
  }
}

async function flushOfflineQueue(): Promise<void> {
  if (isFlushing || !socket || socket.readyState !== WebSocket.OPEN) return;

  isFlushing = true;
  try {
    const frames = await getAllFrames();
    if (frames.length === 0) return;

    console.log(`[VibeCore ServiceWorker] Flushing ${frames.length} queued frames...`);

    for (const frame of frames) {
      if (socket.readyState !== WebSocket.OPEN) break;

      try {
        socket.send(JSON.stringify(frame.message));
        await deleteFrame(frame.id!);
      } catch (err) {
        console.error('[VibeCore ServiceWorker] Failed to flush frame:', err);
        await updateFrameRetry(frame.id!, frame.retry_count + 1);
      }
    }
  } finally {
    isFlushing = false;
  }
}

export async function sendToOrchestrator(msg: BridgeMessage): Promise<void> {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(msg));
  } else {
    console.warn('[VibeCore ServiceWorker] Socket not open; queueing payload:', msg.msg_type);
    await enqueueFrame(msg);
  }
}

/**
 * Handles incoming COMMIT_ACTION frames dispatched from ToolDispatcher
 * (e.g. executing approved WhatsApp send or cart staging).
 */
async function handleCommitAction(msg: BridgeMessage): Promise<void> {
  const { command, recipient, text } = msg.payload as {
    command: string;
    recipient?: string;
    text?: string;
  };

  console.log(`[VibeCore ServiceWorker] Executing verified COMMIT_ACTION: ${command}`);

  let matchPattern = '*://*/*';
  if (command === 'COMMIT_WHATSAPP_SEND') {
    matchPattern = '*://web.whatsapp.com/*';
  }

  const tabs = await chrome.tabs.query({ url: matchPattern });
  const targetTab = tabs[0];

  if (!targetTab || !targetTab.id) {
    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'COMMAND_RESULT',
      payload: {
        command,
        success: false,
        error: `Target tab matching '${matchPattern}' not found for execution.`
      },
      timestamp: new Date().toISOString()
    });
    return;
  }

  try {
    const response = await chrome.tabs.sendMessage(targetTab.id, {
      type: 'EXECUTE_DOM_ACTION',
      command,
      recipient,
      value: text
    });

    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'COMMAND_RESULT',
      tab_id: targetTab.id,
      url: targetTab.url,
      payload: { command, success: true, response },
      timestamp: new Date().toISOString()
    });
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'COMMAND_RESULT',
      tab_id: targetTab.id,
      payload: { command, success: false, error: errorMsg },
      timestamp: new Date().toISOString()
    });
  }
}

async function handleExecuteCommand(msg: BridgeMessage): Promise<void> {
  const { command, selector, value, targetHost } = msg.payload as {
    command: string;
    selector?: string;
    value?: string;
    targetHost?: string;
  };

  const tabs = await chrome.tabs.query({});
  let targetTab = tabs.find(t => t.url && targetHost && t.url.includes(targetHost));

  if (!targetTab && tabs.length > 0) {
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    targetTab = activeTab || tabs[0];
  }

  if (!targetTab || !targetTab.id) {
    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'COMMAND_RESULT',
      payload: { command, success: false, error: 'No active tab found' },
      timestamp: new Date().toISOString()
    });
    return;
  }

  try {
    const response = await chrome.tabs.sendMessage(targetTab.id, {
      type: 'EXECUTE_DOM_ACTION',
      command,
      selector,
      value
    });

    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'COMMAND_RESULT',
      tab_id: targetTab.id,
      url: targetTab.url,
      payload: { command, success: true, response },
      timestamp: new Date().toISOString()
    });
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: 'COMMAND_RESULT',
      tab_id: targetTab.id,
      payload: { command, success: false, error: errorMsg },
      timestamp: new Date().toISOString()
    });
  }
}

// Internal communication listener
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.source === 'vibecore-content-script') {
    // Propagate trace context from content script to orchestrator
    const metadata = message.metadata || {};
    sendToOrchestrator({
      message_id: crypto.randomUUID(),
      msg_type: message.msg_type as BridgeMessageType,
      tab_id: sender.tab?.id,
      url: sender.tab?.url,
      payload: message.payload,
      timestamp: new Date().toISOString(),
      metadata: {
        trace_id: metadata.trace_id,
        span_id: metadata.span_id,
      },
    });
  } else if (message.source === 'vibecore-popup') {
    if (message.type === 'GET_STATUS') {
      sendResponse({ status: connectionStatus });
      return true;
    } else if (message.type === 'RECONNECT') {
      if (socket) {
        socket.close();
        socket = null;
      }
      connectWebSocket().then(() => sendResponse({ status: connectionStatus }));
      return true;
    }
  }
});

// Boot connection
connectWebSocket().catch((err) => console.error('[VibeCore ServiceWorker] Boot error:', err));
