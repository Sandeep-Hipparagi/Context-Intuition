/**
 * Content Script for VibeCore Browser Bridge.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Integrates declarative workflow plugins via the Plugin Registry.
 */

import { pluginRegistry } from '../workflows/plugin_registry';
import { BridgeMessageType, generateTraceId, generateSpanId, BridgeMessageMetadata } from '../types/bridge';

console.log('[VibeCore ContentScript] Active on host:', window.location.hostname);

function sendTelemetry(msgType: BridgeMessageType, payload: unknown): void {
  chrome.runtime.sendMessage({
    source: 'vibecore-content-script',
    msg_type: msgType,
    payload: payload as Record<string, unknown>,
    metadata: { trace_id: generateTraceId(), span_id: generateSpanId() } as BridgeMessageMetadata,
  });
}

// Workflow context for plugins
const workflowContext = {
  sendTelemetry,
  chrome,
  document,
  window,
  log: (msg: string) => console.log(`[VibeCore ContentScript] ${msg}`),
};

// Boot plugin registry
async function bootPluginRegistry(): Promise<void> {
  pluginRegistry.init(workflowContext);
  
  // Initialize plugins for current host
  await pluginRegistry.activateForCurrentHost();

  // Listen for host changes (SPA navigation)
  let lastHost = window.location.hostname;
  setInterval(() => {
    if (window.location.hostname !== lastHost) {
      lastHost = window.location.hostname;
      console.log('[VibeCore ContentScript] Host changed, reactivating plugins');
      pluginRegistry.unmountAll().then(() => pluginRegistry.activateForCurrentHost());
    }
  }, 1000);
}

// Listen for RPC commands dispatched from the Background Service Worker
chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.type === 'EXECUTE_DOM_ACTION') {
    const { command, value } = request;
    
    // Delegate to active plugin
    pluginRegistry.handleCommand(command, value as Record<string, unknown> || {})
      .then(result => sendResponse(result))
      .catch(err => sendResponse({ success: false, error: String(err) }));
    
    return true; // Async response
  }
});

// Handle workflow toggle updates from popup
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local' && changes.workflows) {
    console.log('[VibeCore ContentScript] Workflow toggles changed, reactivating plugins');
    pluginRegistry.unmountAll().then(() => pluginRegistry.activateForCurrentHost());
  }
});

bootPluginRegistry().catch(console.error);