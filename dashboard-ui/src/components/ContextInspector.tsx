import React, { useState, useEffect } from 'react';
import { ContextNode, BehavioralRule, ContextEvent, TraceSpan, TraceSummary, CanaryWarningPayload } from '../types/schema';
import { Brain, Sparkles, Compass, Tag, GitBranch, Clock, Search, AlertTriangle } from 'lucide-react';
import { fetchNodeHistory, fetchRecentTraces, fetchTrace, searchContext } from '../api/client';

interface ContextInspectorProps {
  nodes: ContextNode[];
  rules: BehavioralRule[];
  onRefresh: () => void;
  canaryWarning?: CanaryWarningPayload | null;
  onDismissCanary?: () => void;
}

export const ContextInspector: React.FC<ContextInspectorProps> = ({ nodes, rules, onRefresh, canaryWarning, onDismissCanary }) => {
  const [activeTab, setActiveTab] = useState<'rules' | 'trajectories' | 'nodes' | 'traces' | 'history' | 'search'>('rules');
  const [selectedNode, setSelectedNode] = useState<ContextNode | null>(null);
  const [nodeHistory, setNodeHistory] = useState<ContextEvent[]>([]);
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<TraceSummary | null>(null);
  const [traceSpans, setTraceSpans] = useState<TraceSpan[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingTraces, setLoadingTraces] = useState(false);
  const [loadingSpans, setLoadingSpans] = useState(false);
  const [loadingSearch, setLoadingSearch] = useState(false);
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [searchResults, setSearchResults] = useState<{node: ContextNode, score: number}[]>([]);

  const trajectoryNodes = nodes.filter((n) => n.node_type === 'TRAJECTORY');

  // Load recent traces on mount and refresh
  useEffect(() => {
    const loadTraces = async () => {
      setLoadingTraces(true);
      try {
        const data = await fetchRecentTraces(50);
        setTraces(data);
      } catch (e) {
        console.error('Failed to load traces:', e);
      } finally {
        setLoadingTraces(false);
      }
    };
    loadTraces();
  }, [onRefresh]);

  const handleNodeClick = async (node: ContextNode) => {
    setSelectedNode(node);
    setActiveTab('history');
    setLoadingHistory(true);
    try {
      const history = await fetchNodeHistory(node.node_id);
      setNodeHistory(history);
    } catch (e) {
      console.error('Failed to load node history:', e);
      setNodeHistory([]);
    } finally {
      setLoadingHistory(false);
    }
  };

  const handleTraceClick = async (trace: TraceSummary) => {
    setSelectedTrace(trace);
    setLoadingSpans(true);
    try {
      const spans = await fetchTrace(trace.trace_id);
      setTraceSpans(spans);
    } catch (e) {
      console.error('Failed to load trace spans:', e);
      setTraceSpans([]);
    } finally {
      setLoadingSpans(false);
    }
  };

  const handleSearch = async (query: string) => {
    setLoadingSearch(true);
    setSearchQuery(query);
    try {
      const results = await searchContext(query);
      setSearchResults(results);
    } catch (e) {
      console.error('Failed to search context:', e);
      setSearchResults([]);
    } finally {
      setLoadingSearch(false);
    }
  };

  const formatDuration = (ms: number | undefined) => {
    if (ms === undefined) return 'N/A';
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };

  const formatTimestamp = (ts: string) => {
    try {
      const date = new Date(ts);
      const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const ms = date.getMilliseconds().toString().padStart(3, '0');
      return `${time}.${ms}`;
    } catch {
      return ts;
    }
  };

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl space-y-5">
      {/* Canary Warning Banner */}
      {canaryWarning && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 space-y-2">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-400" />
            <span className="text-xs font-semibold text-amber-300">DOM Canary Warning</span>
          </div>
          <div className="text-xs text-zinc-300 font-mono">
            <span className="text-zinc-500">Plugin:</span> {canaryWarning.pluginName} ({canaryWarning.pluginId})
          </div>
          <div className="text-xs text-zinc-300 font-mono">
            <span className="text-zinc-500">Missing selectors:</span> {canaryWarning.missingSelectors.join(', ')}
          </div>
          <div className="text-[10px] text-zinc-500 font-mono">
            <span className="text-zinc-500">URL:</span> {canaryWarning.url}
          </div>
          <button
            onClick={onDismissCanary}
            className="text-xs text-amber-400 hover:text-amber-300"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800/80 pb-4">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-purple-500/10 p-2.5 border border-purple-500/20">
            <Brain className="h-6 w-6 text-purple-400" />
          </div>
          <div>
            <h2 className="text-base font-bold text-zinc-100">Context Intuition & Edit-Delta Ledger</h2>
            <p className="text-xs text-zinc-400 font-mono">
              Longitudinal Knowledge Graph • Dynamic Preference Induction • Distributed Tracing
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {/* Search Bar */}
          <div className="relative hidden sm:block">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-500" />
            <input
              type="text"
              placeholder="Search context (hybrid semantic + temporal)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && e.currentTarget.value.trim()) {
                  handleSearch(e.currentTarget.value.trim());
                }
              }}
              className="bg-zinc-900 border border-zinc-700 rounded-lg pl-10 pr-4 py-2 text-xs text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-purple-500 w-64"
            />
          </div>
          <button
            onClick={onRefresh}
            className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 transition"
          >
            🔄 Refresh Ledger
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex flex-wrap gap-2 border-b border-zinc-800/60 pb-3">
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
        <button
          onClick={() => setActiveTab('traces')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
            activeTab === 'traces'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <GitBranch className="h-3.5 w-3.5" />
          Trace Waterfall ({traces.length})
        </button>
        {selectedNode && (
          <button
            onClick={() => setActiveTab('history')}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
              activeTab === 'history'
                ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
                : 'text-zinc-400 hover:text-zinc-200'
            }`}
          >
            <Clock className="h-3.5 w-3.5" />
            Node History
          </button>
        )}
        <button
          onClick={() => setActiveTab('search')}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
            activeTab === 'search'
              ? 'bg-purple-600/20 text-purple-300 border border-purple-500/30'
              : 'text-zinc-400 hover:text-zinc-200'
          }`}
        >
          <Search className="h-3.5 w-3.5" />
          Search
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
              onClick={() => handleNodeClick(n)}
              className="flex items-center justify-between rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-2.5 cursor-pointer hover:bg-zinc-900/50 transition"
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

      {activeTab === 'traces' && (
        <div className="space-y-3">
          {loadingTraces ? (
            <div className="text-center py-8 text-zinc-500">
              <div className="animate-spin inline-block h-6 w-6 border-2 border-purple-500 border-t-transparent rounded-full" />
              <p className="mt-2 text-xs">Loading traces...</p>
            </div>
          ) : traces.length === 0 ? (
            <p className="text-xs text-zinc-500 py-4 text-center">
              No traces recorded yet. Traces are generated from Chrome Extension DOM events, WS Bridge, HybridRouter, PolicyEnclave, and ToolDispatcher.
            </p>
          ) : (
            traces.map((trace) => (
              <div
                key={trace.trace_id}
                onClick={() => handleTraceClick(trace)}
                className={`rounded-xl border border-zinc-800/80 bg-zinc-900/40 p-4 space-y-2 cursor-pointer transition ${
                  selectedTrace?.trace_id === trace.trace_id
                    ? 'border-purple-500/50 bg-purple-500/10'
                    : 'hover:border-zinc-700/50 hover:bg-zinc-900/50'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[10px] text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded border border-purple-500/20">
                      {trace.trace_id.slice(0, 16)}…
                    </span>
                    <span className="text-xs text-zinc-400">{formatTimestamp(trace.started_at)}</span>
                  </div>
                  <div className="flex items-center gap-3 text-[11px] text-zinc-500 font-mono">
                    <span>{trace.span_count} spans</span>
                    <span>{trace.max_duration_ms ? `${trace.max_duration_ms}ms` : 'N/A'}</span>
                    <span className="flex flex-wrap gap-1">
                      {trace.components.map(c => (
                        <span key={c} className="bg-zinc-800 px-1.5 py-0.5 rounded text-[9px]">{c}</span>
                      ))}
                    </span>
                  </div>
                </div>
                {selectedTrace?.trace_id === trace.trace_id && (
                  <div className="border-t border-zinc-800/50 pt-3 space-y-2">
                    {loadingSpans ? (
                      <div className="text-center py-4 text-zinc-500">
                        <div className="animate-spin inline-block h-5 w-5 border-2 border-purple-500 border-t-transparent rounded-full" />
                        <p className="mt-1 text-xs">Loading spans...</p>
                      </div>
                    ) : traceSpans.length === 0 ? (
                      <p className="text-xs text-zinc-500">No spans recorded</p>
                    ) : (
                      <div className="space-y-1 max-h-64 overflow-y-auto">
                        {traceSpans.map((span, idx) => (
                          <div
                            key={`${span.trace_id}-${span.span_id}`}
                            className="flex items-center gap-2 text-[10px] font-mono text-zinc-300 px-2 py-1 rounded bg-zinc-950 border border-zinc-800/50"
                          >
                            <span className="text-zinc-500">{idx + 1}.</span>
                            <span className="text-purple-400">{span.component}</span>
                            <span className="text-zinc-400">→</span>
                            <span className="text-emerald-400">{span.operation}</span>
                            <span className={`px-1.5 py-0.5 rounded text-[9px] ${
                              span.status === 'completed' ? 'bg-emerald-500/20 text-emerald-400' :
                              span.status === 'error' ? 'bg-rose-500/20 text-rose-400' :
                              'bg-amber-500/20 text-amber-400'
                            }`}>
                              {span.status}
                            </span>
                            <span className="text-zinc-500 ml-auto">{formatDuration(span.duration_ms)}</span>
                            {span.parent_span_id && (
                              <span className="text-zinc-600">↳ {span.parent_span_id.slice(0, 8)}</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}

      {activeTab === 'history' && selectedNode && (
        <div className="space-y-3">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h4 className="text-xs font-bold text-zinc-200">{selectedNode.title}</h4>
              <p className="text-[10px] text-zinc-500 font-mono">
                Node ID: {selectedNode.node_id.slice(0, 16)}… • Type: {selectedNode.node_type} • Source: {selectedNode.source}
              </p>
            </div>
            <button
              onClick={() => { setSelectedNode(null); setActiveTab('nodes'); }}
              className="text-zinc-500 hover:text-zinc-300 text-xs"
            >
              ← Back to Nodes
            </button>
          </div>
          {loadingHistory ? (
            <div className="text-center py-8 text-zinc-500">
              <div className="animate-spin inline-block h-6 w-6 border-2 border-purple-500 border-t-transparent rounded-full" />
              <p className="mt-2 text-xs">Loading history...</p>
            </div>
          ) : nodeHistory.length === 0 ? (
            <p className="text-xs text-zinc-500 py-4 text-center">
              No history events recorded for this node.
            </p>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {nodeHistory.map((event, idx) => (
                <div
                  key={event.event_id}
                  className="flex items-start gap-3 text-[10px] font-mono text-zinc-300 px-3 py-2 rounded bg-zinc-950 border border-zinc-800/50"
                >
                  <span className="text-zinc-500 shrink-0">{idx + 1}.</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                        event.event_type === 'NODE_CREATED' ? 'bg-emerald-500/20 text-emerald-400' :
                        event.event_type === 'NODE_UPDATED' ? 'bg-blue-500/20 text-blue-400' :
                        event.event_type === 'CONFIDENCE_REINFORCED' ? 'bg-amber-500/20 text-amber-400' :
                        event.event_type === 'NODE_PRUNED' ? 'bg-rose-500/20 text-rose-400' :
                        'bg-purple-500/20 text-purple-400'
                      }`}>
                        {event.event_type}
                      </span>
                      <span className="text-zinc-500">{formatTimestamp(event.created_at)}</span>
                      {event.agent_id && <span className="text-zinc-600">by {event.agent_id}</span>}
                      {event.trace_id && (
                        <span className="text-zinc-600 cursor-pointer hover:text-purple-400" title={event.trace_id}>
                          trace: {event.trace_id.slice(0, 12)}…
                        </span>
                      )}
                    </div>
                    <pre className="text-[9px] text-zinc-400 bg-zinc-900 p-2 rounded border border-zinc-800/50 overflow-x-auto">
                      {JSON.stringify(event.delta_payload, null, 2)}
                    </pre>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === 'search' && (
        <div className="space-y-3">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-xs font-bold text-zinc-200">Hybrid Semantic + Temporal Search</h4>
            <span className="text-[10px] text-zinc-500 font-mono">
              {searchResults.length} result(s) for "{searchQuery}"
            </span>
          </div>
          {loadingSearch ? (
            <div className="text-center py-8 text-zinc-500">
              <div className="animate-spin inline-block h-6 w-6 border-2 border-purple-500 border-t-transparent rounded-full" />
              <p className="mt-2 text-xs">Searching context graph...</p>
            </div>
          ) : searchQuery && searchResults.length === 0 ? (
            <p className="text-xs text-zinc-500 py-4 text-center">
              No results found. Try different keywords.
            </p>
          ) : searchResults.length > 0 ? (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {searchResults.map((result: {node: ContextNode, score: number}, idx: number) => (
                <div
                  key={`${result.node.node_id}-${idx}`}
                  onClick={() => handleNodeClick(result.node)}
                  className="flex items-center justify-between rounded-lg border border-zinc-800/60 bg-zinc-900/30 p-2.5 cursor-pointer hover:bg-zinc-900/50 transition"
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-mono uppercase px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300">
                        {result.node.node_type}
                      </span>
                      <span className="text-xs font-semibold text-zinc-200 line-clamp-1">{result.node.title}</span>
                      <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                        result.score >= 0.7 ? 'bg-emerald-500/20 text-emerald-400' :
                        result.score >= 0.4 ? 'bg-amber-500/20 text-amber-400' :
                        'bg-rose-500/20 text-rose-400'
                      }`}>
                        {Math.round(result.score * 100)}%
                      </span>
                    </div>
                    <div className="text-[10px] text-zinc-500 font-mono">
                      Source: {result.node.source} • Accessed: {result.node.access_count}x
                    </div>
                  </div>
                  <div className="text-right font-mono text-[11px] text-zinc-400">
                    Click to inspect
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-zinc-500 py-4 text-center">
              Enter a query in the search bar above to search the context graph.
            </p>
          )}
        </div>
      )}
    </div>
  );
};