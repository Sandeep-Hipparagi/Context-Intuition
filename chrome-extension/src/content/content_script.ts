/**
 * Content Script for VibeCore Browser Bridge.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Integrates WhatsApp Web Co-Pilot, Grocery & E-Commerce Price Tracker,
 * and Instagram Archivist workflows directly into the active browser profile.
 */

import { GroceryTrackerWorkflow } from '../workflows/grocery_tracker';
import { WhatsAppCopilotWorkflow } from '../workflows/whatsapp_copilot';
import { InstagramArchivistWorkflow } from '../workflows/instagram_archivist';
import { BridgeMessageType } from '../types/bridge';

console.log('[VibeCore ContentScript] Active on host:', window.location.hostname);

const groceryWorkflow = new GroceryTrackerWorkflow();
const whatsappWorkflow = new WhatsAppCopilotWorkflow((msg) => {
  chrome.runtime.sendMessage({
    source: 'vibecore-content-script',
    msg_type: 'WHATSAPP_INCOMING_MESSAGE' as BridgeMessageType,
    payload: msg,
  });
});

const instagramWorkflow = new InstagramArchivistWorkflow((batch) => {
  chrome.runtime.sendMessage({
    source: 'vibecore-content-script',
    msg_type: 'INSTAGRAM_SAVED_BATCH' as BridgeMessageType,
    payload: { items: batch },
  });
});

// Boot active workflows according to user storage preferences
async function bootWorkflows(): Promise<void> {
  let toggles = { whatsapp_copilot: true, grocery_tracker: true, instagram_archivist: true };
  try {
    const stored = await chrome.storage.local.get(['workflows']);
    if (stored.workflows) toggles = { ...toggles, ...stored.workflows };
  } catch (e) {
    // Chrome storage fallback
  }

  // 1. WhatsApp Web Co-Pilot
  if (whatsappWorkflow.isWhatsAppPage() && toggles.whatsapp_copilot) {
    whatsappWorkflow.startObservation();
  }

  // 2. Grocery & E-Commerce Tracker
  if (groceryWorkflow.isSupportedPage() && toggles.grocery_tracker) {
    setTimeout(() => {
      const snapshot = groceryWorkflow.extractProductSnapshot();
      if (snapshot) {
        chrome.runtime.sendMessage({
          source: 'vibecore-content-script',
          msg_type: 'DOM_GROCERY_SNAPSHOT' as BridgeMessageType,
          payload: snapshot,
        });
      }
    }, 1500);
  }

  // 3. Instagram Archivist
  if (instagramWorkflow.isSavedPage() && toggles.instagram_archivist) {
    instagramWorkflow.startArchiving().catch(console.error);
  }
}

// Listen for RPC commands dispatched from the Background Service Worker
chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.type === 'EXECUTE_DOM_ACTION') {
    const { command, value } = request;

    // TIER_1_STAGE: WhatsApp Draft Injection
    if (command === 'STAGE_WHATSAPP_DRAFT') {
      const res = whatsappWorkflow.stageDraft(value || '');
      sendResponse(res);
      return true;
    }

    // TIER_2_COMMIT: WhatsApp Send Execution (Authorized by Phase 2 ToolDispatcher)
    if (command === 'COMMIT_WHATSAPP_SEND') {
      const res = whatsappWorkflow.commitSendMessage();
      sendResponse(res);
      return true;
    }

    // TIER_1_STAGE: Add to Cart
    if (command === 'STAGE_ADD_TO_CART') {
      const res = groceryWorkflow.stageAddToCart();
      sendResponse(res);
      return true;
    }

    // Manual Scan Trigger from Popup UI
    if (command === 'SCAN_CURRENT_TAB') {
      if (groceryWorkflow.isSupportedPage()) {
        const snapshot = groceryWorkflow.extractProductSnapshot();
        if (snapshot) {
          chrome.runtime.sendMessage({
            source: 'vibecore-content-script',
            msg_type: 'DOM_GROCERY_SNAPSHOT' as BridgeMessageType,
            payload: snapshot,
          });
          sendResponse({ success: true, message: `Scanned product: ${snapshot.title}` });
          return true;
        }
      }
      sendResponse({ success: false, message: 'No supported workflow content detected on current tab.' });
      return true;
    }

    sendResponse({ success: false, error: `Unrecognized command: ${command}` });
    return true;
  }
});

bootWorkflows().catch(console.error);
