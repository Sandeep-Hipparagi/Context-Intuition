/**
 * WhatsApp Web 24/7 Co-Pilot Workflow.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Tier 0: Non-polling DOM MutationObserver streaming unread/incoming messages.
 * Tier 1: Safely injects drafted text into the active conversation composer without sending.
 * Tier 2: Executes Send button click only upon signed, explicit approval frame (COMMIT_ACTION).
 */

import { WhatsAppMessageSnapshot, BridgeMessageType, generateTraceId, generateSpanId, BridgeMessageMetadata } from '../types/bridge';

export class WhatsAppCopilotWorkflow {
  private lastMessageId: string = '';
  private observer: MutationObserver | null = null;
  private onMessageCallback: (msg: WhatsAppMessageSnapshot) => void;

  constructor(onMessageCallback: (msg: WhatsAppMessageSnapshot) => void) {
    this.onMessageCallback = onMessageCallback;
  }

  public isWhatsAppPage(): boolean {
    return window.location.hostname.includes('web.whatsapp.com');
  }

  /**
   * TIER_0_OBSERVE: Attaches debounced MutationObserver to active chat window.
   */
  public startObservation(): void {
    if (!this.isWhatsAppPage() || this.observer) return;

    console.log('[VibeCore WhatsApp] Initializing real-time DOM mutation observer...');

    let debounceTimer: number | null = null;

    this.observer = new MutationObserver(() => {
      if (debounceTimer) clearTimeout(debounceTimer);

      debounceTimer = window.setTimeout(() => {
        this.scanForNewIncomingMessages();
      }, 400);
    });

    this.observer.observe(document.body, {
      childList: true,
      subtree: true,
    });
  }

  public stopObservation(): void {
    if (this.observer) {
      this.observer.disconnect();
      this.observer = null;
    }
  }

  /**
   * TIER_1_STAGE: Injects draft text into WhatsApp active composer without clicking send.
   */
  public stageDraft(draftText: string): { success: boolean; message: string } {
    const composer = this.findComposerElement();
    if (!composer) {
      return { success: false, message: 'WhatsApp composer input element not found.' };
    }

    composer.focus();

    // Use document.execCommand to maintain WhatsApp React/Draft.js synthetic event bindings
    document.execCommand('selectAll', false);
    document.execCommand('insertText', false, draftText);

    // Dispatch input event to ensure React state syncs
    composer.dispatchEvent(new Event('input', { bubbles: true }));

    return {
      success: true,
      message: 'Draft staged in WhatsApp composer (TIER_1_STAGE). Manual or signed commit required to send.'
    };
  }

  /**
   * TIER_2_COMMIT: Dispatches Send button click ONLY when explicitly authorized.
   */
  public commitSendMessage(): { success: boolean; message: string } {
    const sendButton = this.findSendButton();
    if (!sendButton) {
      return { success: false, message: 'Send button not found or disabled.' };
    }

    sendButton.click();
    return {
      success: true,
      message: 'Message dispatched successfully via approved TIER_2_COMMIT action.'
    };
  }

  // ==========================================================================
  // Internal DOM Helpers
  // ==========================================================================

  private scanForNewIncomingMessages(): void {
    // Incoming message bubbles in WhatsApp Web typically have class 'message-in'
    const incomingBubbles = document.querySelectorAll('div.message-in');
    if (!incomingBubbles.length) return;

    const latestBubble = incomingBubbles[incomingBubbles.length - 1] as HTMLElement;
    const textEl = latestBubble.querySelector('.selectable-text, span[dir="ltr"]');
    const text = textEl?.textContent?.trim() || '';

    if (!text) return;

    // Generate pseudo-unique message hash
    const chatHeader = document.querySelector('header span[dir="auto"]')?.textContent?.trim() || 'Unknown Chat';
    const messageKey = `${chatHeader}:${text.slice(0, 40)}`;

    if (messageKey === this.lastMessageId) return;
    this.lastMessageId = messageKey;

    const snapshot: WhatsAppMessageSnapshot = {
      app: 'whatsapp_web',
      chat_name: chatHeader,
      sender: chatHeader,
      text,
      timestamp: new Date().toISOString(),
      is_group: document.querySelector('header span[data-icon="default-group"]') !== null
    };

    console.log('[VibeCore WhatsApp] Detected new message:', snapshot.chat_name, text.slice(0, 30));
    this.onMessageCallback(snapshot);
  }

  private findComposerElement(): HTMLElement | null {
    return document.querySelector(
      'footer div[contenteditable="true"][role="textbox"], div[data-tab="10"][contenteditable="true"]'
    );
  }

  private findSendButton(): HTMLElement | null {
    return document.querySelector(
      'button[aria-label="Send"], span[data-icon="send"], button[data-tab="11"]'
    );
  }
}

// Make globally accessible for plugin registry
(window as any).__whatsappWorkflow = new WhatsAppCopilotWorkflow((msg) => {
  chrome.runtime.sendMessage({
    source: 'vibecore-content-script',
    msg_type: 'WHATSAPP_INCOMING_MESSAGE' as BridgeMessageType,
    payload: msg,
    metadata: { trace_id: generateTraceId(), span_id: generateSpanId() } as BridgeMessageMetadata,
  });
});
