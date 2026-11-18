/**
 * VibeCore Live Personal Dashboard Main Application.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 */

import React, { useState, useEffect } from 'react';
import {
  ActionEnvelope,
  ApprovalStatus,
  BehavioralRule,
  ContextNode,
  MorningBrief,
} from './types/schema';
import {
  fetchPendingApprovals,
  fetchLatestMorningBrief,
  fetchContextNodes,
  fetchBehavioralRules,
  resolveApprovalAction,
  triggerMorningBrief,
  wsClient,
  WsConnectionState,
} from './api/client';
import { TelemetryBar } from './components/TelemetryBar';
import { MorningBriefCard } from './components/MorningBriefCard';
import { ApprovalQueue } from './components/ApprovalQueue';
import { ContextInspector } from './components/ContextInspector';
import { SettingsModal } from './components/SettingsModal';
import { Sparkles, Bell } from 'lucide-react';

export const App: React.FC = () => {
  const [wsState, setWsState] = useState<WsConnectionState>('DISCONNECTED');
  const [pendingApprovals, setPendingApprovals] = useState<ActionEnvelope[]>([]);
  const [morningBrief, setMorningBrief] = useState<MorningBrief | null>(null);
  const [contextNodes, setContextNodes] = useState<ContextNode[]>([]);
  const [behavioralRules, setBehavioralRules] = useState<BehavioralRule[]>([]);
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);
  const [liveNotification, setLiveNotification] = useState<string | null>(null);
  const [isLoadingBrief, setIsLoadingBrief] = useState<boolean>(false);

  // Initialize data and WebSocket connection
  useEffect(() => {
    loadAllData();

    // Subscribe to WS connection states
    const unsubState = wsClient.subscribeState(setWsState);

    // Subscribe to live backend events
    const unsubEvents = wsClient.subscribe((event, data) => {
      console.log('Live Swarm Event:', event, data);

      if (event === 'INITIAL_PENDING_APPROVALS') {
        setPendingApprovals(data);
      } else if (event === 'ACTION_RESOLVED') {
        setPendingApprovals((prev) => prev.filter((a) => a.action_id !== data.action_id));
        loadLedgerData();
      } else if (event === 'NEW_MORNING_BRIEF') {
        setMorningBrief(data);
        showNotification('Overnight Chief-of-Staff completed synthesis cycle.');
        loadLedgerData();
      } else if (event === 'NEW_WHATSAPP_MESSAGE') {
        showNotification(`New message from ${data.chat_name}: "${data.text.slice(0, 40)}..."`);
        loadLedgerData();
      } else if (event === 'GROCERY_PRICE_UPDATE') {
        showNotification(`Price updated: ${data.title} ($${data.unit_price})`);
        loadLedgerData();
      }
    });

    wsClient.connect();

    return () => {
      unsubState();
      unsubEvents();
      wsClient.disconnect();
    };
  }, []);

  const showNotification = (msg: string) => {
    setLiveNotification(msg);
    setTimeout(() => setLiveNotification(null), 5000);
  };

  const loadAllData = async () => {
    try {
      const [approvals, brief] = await Promise.all([
        fetchPendingApprovals().catch(() => []),
        fetchLatestMorningBrief().catch(() => null),
      ]);
      setPendingApprovals(approvals);
      setMorningBrief(brief);
    } catch (e) {
      console.error('Error loading primary dashboard data:', e);
    }
    loadLedgerData();
  };

  const loadLedgerData = async () => {
    try {
      const [nodes, rules] = await Promise.all([
        fetchContextNodes().catch(() => []),
        fetchBehavioralRules().catch(() => []),
      ]);
      setContextNodes(nodes);
      setBehavioralRules(rules);
    } catch (e) {
      console.error('Error loading context ledger:', e);
    }
  };

  const handleResolveAction = async (
    actionId: string,
    status: ApprovalStatus,
    editedPayload?: Record<string, unknown>
  ) => {
    try {
      await resolveApprovalAction(actionId, status, 'sig_dashboard_user', editedPayload);
      setPendingApprovals((prev) => prev.filter((a) => a.action_id !== actionId));
      loadLedgerData();
      showNotification(`Action ${status.toLowerCase()} and executed.`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      alert(`Approval error: ${msg}`);
    }
  };

  const handleTriggerBrief = async () => {
    setIsLoadingBrief(true);
    try {
      const newBrief = await triggerMorningBrief();
      setMorningBrief(newBrief);
      showNotification('Fresh Morning Brief generated.');
      loadLedgerData();
    } catch (e) {
      alert('Failed to trigger morning brief cycle.');
    } finally {
      setIsLoadingBrief(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col font-sans">
      {/* Telemetry Bar */}
      <TelemetryBar
        wsState={wsState}
        pendingCount={pendingApprovals.length}
        onOpenSettings={() => setIsSettingsOpen(true)}
      />

      {/* Live Toast Notification Banner */}
      {liveNotification && (
        <div className="bg-emerald-500/10 border-b border-emerald-500/20 px-6 py-2.5 flex items-center justify-between text-xs text-emerald-300 font-mono">
          <div className="flex items-center gap-2">
            <Bell className="h-3.5 w-3.5 text-emerald-400 animate-bounce" />
            <span>{liveNotification}</span>
          </div>
          <button onClick={() => setLiveNotification(null)} className="text-zinc-500 hover:text-zinc-300">
            Dismiss
          </button>
        </div>
      )}

      {/* Main Grid Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
        {/* Top Control Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-zinc-100 tracking-tight">Executive Cockpit</h1>
            <p className="text-xs text-zinc-500 font-mono">
              Longitudinal Context Engine • Zero-Trust Policy Enclave
            </p>
          </div>
          <button
            onClick={handleTriggerBrief}
            disabled={isLoadingBrief}
            className="flex items-center gap-2 rounded-xl bg-purple-600/20 border border-purple-500/30 px-4 py-2 text-xs font-semibold text-purple-300 hover:bg-purple-600/30 transition shadow-sm disabled:opacity-50"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {isLoadingBrief ? 'Synthesizing...' : 'Trigger Morning Brief Now'}
          </button>
        </div>

        {/* Priority HITL Approval Queue */}
        <ApprovalQueue
          actions={pendingApprovals}
          onResolve={handleResolveAction}
        />

        {/* Two Column Layout: Morning Brief & Context Ledger */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
          {/* Overnight Chief-of-Staff Brief */}
          {morningBrief ? (
            <MorningBriefCard
              brief={morningBrief}
              onApproveAction={(actionId) => handleResolveAction(actionId, 'APPROVED')}
            />
          ) : (
            <div className="rounded-2xl border border-zinc-800 bg-zinc-950 p-6 text-center text-zinc-500 text-xs">
              No morning brief synthesized yet. Click 'Trigger Morning Brief Now' to compile.
            </div>
          )}

          {/* Context Intuition & Edit-Delta Ledger */}
          <ContextInspector
            nodes={contextNodes}
            rules={behavioralRules}
            onRefresh={loadLedgerData}
          />
        </div>
      </main>

      {/* Settings Modal */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onSaveToken={() => {
          wsClient.disconnect();
          wsClient.connect();
        }}
      />
    </div>
  );
};

export default App;
