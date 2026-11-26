/**
 * Instagram Saved Post & Reel Archivist Workflow.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Tier 0: Human-paced, jittered scroll indexer for saved Instagram posts/collections.
 * Features rate-limit backoff, alt-text extraction, and batch telemetry transmission.
 */

import { InstagramSavedItem, BridgeMessageType, generateTraceId, generateSpanId, BridgeMessageMetadata } from '../types/bridge';

export class InstagramArchivistWorkflow {
  private isRunning: boolean = false;
  private indexedUrls: Set<string> = new Set();
  private onBatchCallback: (items: InstagramSavedItem[]) => void;

  constructor(onBatchCallback: (items: InstagramSavedItem[]) => void) {
    this.onBatchCallback = onBatchCallback;
  }

  public isSavedPage(): boolean {
    const path = window.location.pathname.toLowerCase();
    return window.location.hostname.includes('instagram.com') && (path.includes('/saved') || path.includes('/collections'));
  }

  /**
   * Starts periodic human-paced indexing with randomized jitter (2500ms - 4500ms).
   */
  public async startArchiving(): Promise<void> {
    if (!this.isSavedPage() || this.isRunning) return;

    this.isRunning = true;
    console.log('[VibeCore Instagram] Starting saved post indexing cycle...');

    while (this.isRunning) {
      if (this.detectRateLimitBanner()) {
        console.warn('[VibeCore Instagram] Rate-limit detected! Backing off for 60 seconds...');
        await this.sleep(60000);
        continue;
      }

      const batch = this.extractVisiblePosts();
      if (batch.length > 0) {
        console.log(`[VibeCore Instagram] Extracted batch of ${batch.length} saved post(s).`);
        this.onBatchCallback(batch);
      }

      // Human-paced scroll with randomized jitter
      const scrollJitter = 2500 + Math.random() * 2000; // 2500ms - 4500ms
      window.scrollBy({
        top: 600 + Math.floor(Math.random() * 200),
        behavior: 'smooth'
      });

      await this.sleep(scrollJitter);
    }
  }

  public stopArchiving(): void {
    this.isRunning = false;
    console.log('[VibeCore Instagram] Archivist stopped.');
  }

  /**
   * Scrapes visible post elements currently in the DOM.
   */
  public extractVisiblePosts(): InstagramSavedItem[] {
    const postLinks = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
    const newItems: InstagramSavedItem[] = [];

    for (const el of Array.from(postLinks)) {
      const link = el as HTMLAnchorElement;
      const href = link.href.split('?')[0];

      if (!this.indexedUrls.has(href)) {
        this.indexedUrls.add(href);

        const img = link.querySelector('img');
        const altText = img?.getAttribute('alt') || '';
        const authorMatch = altText.match(/photo by ([^.]+)/i) || altText.match(/reel by ([^.]+)/i);
        const author = authorMatch ? authorMatch[1].trim() : 'instagram_creator';

        newItems.push({
          post_url: href,
          author,
          caption: altText,
          alt_text: altText,
          extracted_at: new Date().toISOString()
        });
      }
    }

    return newItems;
  }

  private detectRateLimitBanner(): boolean {
    const text = document.body.innerText.toLowerCase();
    return (
      text.includes('try again later') ||
      text.includes('action blocked') ||
      text.includes('we restrict certain activity')
    );
  }

  private sleep(ms: number): Promise<void> {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// Make globally accessible for plugin registry
(window as any).__instagramWorkflow = new InstagramArchivistWorkflow((batch) => {
  chrome.runtime.sendMessage({
    source: 'vibecore-content-script',
    msg_type: 'INSTAGRAM_SAVED_BATCH' as BridgeMessageType,
    payload: { items: batch },
    metadata: { trace_id: generateTraceId(), span_id: generateSpanId() } as BridgeMessageMetadata,
  });
});
