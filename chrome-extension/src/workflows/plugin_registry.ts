/**
 * Workflow Plugin Registry for VibeCore.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Declarative plugin system for browser workflows with DOM canary health checks.
 */

import { BridgeMessageType, generateTraceId } from '../types/bridge';
import { WhatsAppCopilotWorkflow } from './whatsapp_copilot';
import { GroceryTrackerWorkflow } from './grocery_tracker';
import { InstagramArchivistWorkflow } from './instagram_archivist';

export interface WorkflowContext {
  sendTelemetry: (msgType: BridgeMessageType, payload: Record<string, unknown>) => void;
  chrome: typeof chrome;
  document: Document;
  window: Window;
  log: (msg: string) => void;
}

function toRecord(obj: unknown): Record<string, unknown> {
  return obj as Record<string, unknown>;
}

export interface WorkflowPlugin {
  /** Unique identifier for the plugin */
  id: string;
  /** Human-readable name */
  name: string;
  /** URL patterns this plugin should activate on */
  hostPatterns: RegExp[];
  /** Critical DOM selectors that must exist for the plugin to work (canary checks) */
  canarySelectors: string[];
  /** Called when plugin mounts on a matching host */
  onMount: (ctx: WorkflowContext) => void | Promise<void>;
  /** Called when a command is received from the background service worker */
  onCommand: (command: string, payload: Record<string, unknown>) => { success: boolean; message?: string; response?: unknown } | Promise<{ success: boolean; message?: string; response?: unknown }>;
  /** Called when plugin unmounts (navigation away from host) */
  onUnmount: () => void | Promise<void>;
}

export interface CanaryWarningPayload {
  pluginId: string;
  pluginName: string;
  missingSelectors: string[];
  url: string;
  timestamp: string;
  trace_id: string;
}

class WorkflowPluginRegistry {
  private plugins: Map<string, WorkflowPlugin> = new Map();
  private activePlugin: WorkflowPlugin | null = null;
  private context: WorkflowContext | null = null;

  /**
   * Register a workflow plugin
   */
  register(plugin: WorkflowPlugin): void {
    if (this.plugins.has(plugin.id)) {
      console.warn(`[PluginRegistry] Plugin ${plugin.id} already registered, overwriting`);
    }
    this.plugins.set(plugin.id, plugin);
    console.log(`[PluginRegistry] Registered plugin: ${plugin.id} (${plugin.name})`);
  }

  /**
   * Get all registered plugins
   */
  getAll(): WorkflowPlugin[] {
    return Array.from(this.plugins.values());
  }

  /**
   * Get plugin by ID
   */
  get(id: string): WorkflowPlugin | undefined {
    return this.plugins.get(id);
  }

  /**
   * Initialize the registry with a workflow context
   */
  init(context: WorkflowContext): void {
    this.context = context;
  }

  /**
   * Activate plugins for the current host
   */
  async activateForCurrentHost(): Promise<void> {
    if (!this.context) {
      console.warn('[PluginRegistry] No context initialized');
      return;
    }

    const hostname = window.location.hostname;
    const matchingPlugins = this.getAll().filter(p => 
      p.hostPatterns.some(pattern => pattern.test(hostname))
    );

    // Unmount previous active plugin if different
    if (this.activePlugin && !matchingPlugins.some(p => p.id === this.activePlugin!.id)) {
      await this.activePlugin.onUnmount();
      this.activePlugin = null;
    }

    for (const plugin of matchingPlugins) {
      // Check if already active
      if (this.activePlugin?.id === plugin.id) {
        continue;
      }

      try {
        // Run canary health check
        const canaryResult = this.runCanaryCheck(plugin);
        if (!canaryResult.healthy) {
          await this.emitCanaryWarning(plugin, canaryResult.missing);
        }

        // Mount the plugin
        await plugin.onMount(this.context);
        this.activePlugin = plugin;
        console.log(`[PluginRegistry] Activated plugin: ${plugin.id}`);
      } catch (err) {
        console.error(`[PluginRegistry] Failed to activate plugin ${plugin.id}:`, err);
      }
    }
  }

  /**
   * Run DOM canary health check for a plugin
   */
  private runCanaryCheck(plugin: WorkflowPlugin): { healthy: boolean; missing: string[] } {
    const missing: string[] = [];
    for (const selector of plugin.canarySelectors) {
      try {
        const element = document.querySelector(selector);
        if (!element) {
          missing.push(selector);
        }
      } catch (e) {
        // Invalid selector
        missing.push(selector);
      }
    }
    return {
      healthy: missing.length === 0,
      missing,
    };
  }

  /**
   * Emit SELECTOR_CANARY_WARNING frame via WebSocket bridge
   */
  private async emitCanaryWarning(plugin: WorkflowPlugin, missingSelectors: string[]): Promise<void> {
    if (!this.context) return;

    const payload: CanaryWarningPayload = {
      pluginId: plugin.id,
      pluginName: plugin.name,
      missingSelectors,
      url: window.location.href,
      timestamp: new Date().toISOString(),
      trace_id: generateTraceId(),
    };

    this.context.sendTelemetry('SELECTOR_CANARY_WARNING', toRecord(payload));
    console.warn(`[PluginRegistry] Canary warning for ${plugin.id}: missing selectors`, missingSelectors);
  }

  /**
   * Handle incoming command from background service worker
   */
  async handleCommand(command: string, payload: Record<string, unknown>): Promise<{ success: boolean; message?: string; response?: unknown }> {
    if (!this.activePlugin) {
      return { success: false, message: 'No active plugin for current host' };
    }

    try {
      return await this.activePlugin.onCommand(command, payload);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { success: false, message: `Plugin command failed: ${msg}` };
    }
  }

  /**
   * Unmount all plugins (on page unload or context change)
   */
  async unmountAll(): Promise<void> {
    if (this.activePlugin) {
      await this.activePlugin.onUnmount();
      this.activePlugin = null;
    }
  }
}

// Singleton instance
export const pluginRegistry = new WorkflowPluginRegistry();

// ============================================================================
// Built-in Plugin Registrations
// ============================================================================

/**
 * WhatsApp Web Co-Pilot Plugin
 */
const whatsappPlugin: WorkflowPlugin = {
  id: 'whatsapp_copilot',
  name: 'WhatsApp Web Co-Pilot',
  hostPatterns: [/web\.whatsapp\.com/],
  canarySelectors: [
    'div[role="main"]',
    'footer div[contenteditable="true"]',
    'div.message-in',
  ],

  onMount: async (ctx) => {
    const workflow = new WhatsAppCopilotWorkflow((msg) => {
      ctx.sendTelemetry('WHATSAPP_INCOMING_MESSAGE', toRecord(msg));
    });
    workflow.startObservation();
    console.log('[WhatsAppPlugin] Mounted and observing');
  },

  onCommand: (command, payload) => {
    const workflow = (window as any).__whatsappWorkflow;
    if (!workflow) {
      return { success: false, message: 'WhatsApp workflow not initialized' };
    }

    switch (command) {
      case 'STAGE_WHATSAPP_DRAFT':
        return workflow.stageDraft(payload.value as string || '');
      case 'COMMIT_WHATSAPP_SEND':
        return workflow.commitSendMessage();
      default:
        return { success: false, message: `Unknown command: ${command}` };
    }
  },

  onUnmount: async () => {
    const workflow = (window as any).__whatsappWorkflow;
    if (workflow) {
      workflow.stopObservation();
    }
    console.log('[WhatsAppPlugin] Unmounted');
  },
};

/**
 * Grocery & E-Commerce Tracker Plugin
 */
const groceryPlugin: WorkflowPlugin = {
  id: 'grocery_tracker',
  name: 'Grocery & E-Commerce Tracker',
  hostPatterns: [
    /amazon\./,
    /instacart\./,
    /target\./,
    /walmart\./,
  ],
  canarySelectors: [
    'script[type="application/ld+json"]',
    'meta[property*="price"]',
    '#productTitle',
    '.a-price-whole',
  ],

  onMount: async (ctx) => {
    const workflow = new GroceryTrackerWorkflow();
    // Initial scan
    setTimeout(() => {
      const snapshot = workflow.extractProductSnapshot();
      if (snapshot) {
        ctx.sendTelemetry('DOM_GROCERY_SNAPSHOT', toRecord(snapshot));
      }
    }, 1500);
    console.log('[GroceryPlugin] Mounted and scanning');
  },

  onCommand: (command, payload) => {
    const workflow = (window as any).__groceryWorkflow;
    if (!workflow) {
      return { success: false, message: 'Grocery workflow not initialized' };
    }

    switch (command) {
      case 'STAGE_ADD_TO_CART':
        return workflow.stageAddToCart();
      case 'SCAN_CURRENT_TAB':
        const snapshot = workflow.extractProductSnapshot();
        if (snapshot) {
          return { success: true, message: `Scanned: ${snapshot.title}`, response: snapshot };
        }
        return { success: false, message: 'No product found on current page' };
      default:
        return { success: false, message: `Unknown command: ${command}` };
    }
  },

  onUnmount: async () => {
    console.log('[GroceryPlugin] Unmounted');
  },
};

/**
 * Instagram Archivist Plugin
 */
const instagramPlugin: WorkflowPlugin = {
  id: 'instagram_archivist',
  name: 'Instagram Saved Posts Archivist',
  hostPatterns: [/www\.instagram\.com\/saved/],
  canarySelectors: [
    'div[role="feed"]',
    'article div[role="button"]',
  ],

  onMount: async (ctx) => {
    const workflow = new InstagramArchivistWorkflow((batch) => {
      ctx.sendTelemetry('INSTAGRAM_SAVED_BATCH', toRecord({ items: batch }));
    });
    await workflow.startArchiving();
    console.log('[InstagramPlugin] Mounted and archiving');
  },

  onCommand: (command, payload) => {
    const workflow = (window as any).__instagramWorkflow;
    if (!workflow) {
      return { success: false, message: 'Instagram workflow not initialized' };
    }

    switch (command) {
      case 'SCAN_CURRENT_TAB':
        // Instagram archivist runs continuously, just trigger a manual batch
        return { success: true, message: 'Instagram archival continuous, check logs' };
      default:
        return { success: false, message: `Unknown command: ${command}` };
    }
  },

  onUnmount: async () => {
    console.log('[InstagramPlugin] Unmounted');
  },
};

// Register built-in plugins
pluginRegistry.register(whatsappPlugin);
pluginRegistry.register(groceryPlugin);
pluginRegistry.register(instagramPlugin);

// Export for debugging
(window as any).pluginRegistry = pluginRegistry;