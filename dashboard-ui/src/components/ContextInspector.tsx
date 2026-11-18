import React, { useState } from 'react';
import { ContextNode, BehavioralRule } from '../types/schema';
import { Brain, Sparkles, Compass, Tag } from 'lucide-react';

interface ContextInspectorProps {
  nodes: ContextNode[];
  rules: BehavioralRule[];
  onRefresh: () => void;
}

export const ContextInspector: React.FC<ContextInspectorProps> = ({ nodes, rules, onRefresh }) => {
  const [activeTab, setActiveTab] = useState<'rules' | 'trajectories' | 'nodes'>('rules');

  const trajectoryNodes = nodes.filter((n) => n.node_type === 'TRAJECTORY');

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800/80 pb-4">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-purple-500/10 p-2.5 border border-purple-500/20">
            <Brain className="h-6 w-6 text-purple-400" />
          </div>
          <div>
            <h2 className="text-base font-bold text-zinc-100">Context Intuition & Edit-Delta Ledger</h2>
            <p className="text-xs text-zinc-400 font-mono">
              Longitudinal Knowledge Graph • Dynamic Preference Induction
            </p>
          </div>
        </div>
        <button
          onClick={onRefresh}
          className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 transition"
        >
          🔄 Refresh Ledger
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b border-zinc-800/60 pb-3">
        <button
          onClick={() => setActiveTab('rules')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
            activeTab === 'rules'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Sparkles className="h-3.5 w-3.5" />
          Learned Rules ({rules.length})
        </button>
        <button
          onClick={() => setActiveTab('trajectories')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
            activeTab === 'trajectories'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Compass className="h-3.5 w-3.5" />
          Strategic Trajectories ({trajectoryNodes.length})
        </button>
        <button
          onClick={() => setActiveTab('nodes')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
            activeTab === 'nodes'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Tag className="h-3.5 w-3.5" />
          All Memory Nodes ({nodes.length})
        </button>
      </div>

      {/* Content */}
      {activeTab === 'rules' && (
        <div className="space-y-3">
          {rules.length === 0 ? (
            <p className="text-xs text-zinc-500 py-4 text-center">
              No Behavioral Rules induced yet. Modify a staged email or DM in the Approval Queue to trigger Edit-Delta learning.
            </p>
          ) : (
            rules.map((rule) => (
              <div key={rule.rule_id} className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] font-semibold text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded border border-purple-500/20">
                    {rule.category}
                  </span>
                  <span className="font-mono text-[11px] text-zinc-500">
                    Weight: {rule.weight.toFixed(1)}x • Re-enforced: {rule.reinforcement_count}x
                  </span>
                </div>
                <div className="text-xs font-mono text-zinc-400">
                  <span className="text-zinc-500">Trigger:</span> {rule.trigger_pattern}
                </div>
                <div className="rounded-lg bg-zinc-950 p-2.5 border border-zinc-800/60 text-xs font-medium text-emerald-300 font-mono">
                  ↳ Directive: "{rule.action_directive}"
                </div>
                <div className="text-[11px] text-zinc-500 italic">
                  Rationale: {rule.inferred_delta_rationale}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === 'trajectories' && (
        <div className="space-y-3">
          {trajectoryNodes.length === 0 ? (
            <p className="text-xs text-zinc-500 py-4 text-center">
              No consolidated trajectories generated yet. Trajectories are consolidated during overnight sleep passes.
            </p>
          ) : (
            trajectoryNodes.map((t) => (
              <div key={t.node_id} className="rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="text-xs font-bold text-zinc-200">{t.title}</h4>
                  <span className="text-[11px] text-zinc-500 font-mono">Source: {t.source}</span>
                </div>
                <pre className="text-[11px] text-zinc-300 font-mono whitespace-pre-wrap bg-zinc-950 p-2.5 rounded border border-zinc-800/60">
                  {typeof t.content.summary === 'string'
                    ? t.content.summary
                    : JSON.stringify(t.content, null, 2)}
                </pre>
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === 'nodes' && (
        <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
          {nodes.map((n) => (
            <div
              key={n.node_id}
              className="flex items-center justify-between rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-2.5"
            >
              <div className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300">
                    {n.node_type}
                  </span>
                  <span className="text-xs font-semibold text-zinc-200 line-clamp-1">{n.title}</span>
                </div>
                <div className="text-[10px] text-zinc-500 font-mono">
                  Accessed: {n.access_count}x • Source: {n.source}
                </div>
              </div>
              <div className="text-right font-mono text-[11px] text-zinc-400">
                Confidence: {(n.confidence * 100).toFixed(0)}%
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
