import React from 'react';
import { MorningBrief } from '../types/schema';
import { Sun, Calendar, Mail, AlertTriangle, ExternalLink, Zap, Check } from 'lucide-react';

interface MorningBriefCardProps {
  brief: MorningBrief;
  onApproveAction: (actionId: string) => Promise<void>;
}

export const MorningBriefCard: React.FC<MorningBriefCardProps> = ({ brief, onApproveAction }) => {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-800/80 pb-4">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-amber-500/10 p-2.5 border border-amber-500/20">
            <Sun className="h-6 w-6 text-amber-400" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-zinc-100">Overnight Chief-of-Staff Brief</h1>
            <p className="text-xs text-zinc-400 font-mono">
              Consolidated at {new Date(brief.generated_at).toLocaleTimeString()} • 72-Hour Horizon
            </p>
          </div>
        </div>
        <span className="rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-400 border border-emerald-500/20">
          Autonomous Cycle Complete
        </span>
      </div>

      {/* Executive Summary */}
      <div className="rounded-xl bg-zinc-900/60 p-4 border border-zinc-800 text-sm text-zinc-300 leading-relaxed">
        {brief.executive_summary}
      </div>

      {/* Commitments & Staged Replies */}
      {brief.commitments_detected.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Mail className="h-4 w-4 text-blue-400" />
            <h3 className="text-sm font-semibold text-zinc-200">Unspoken Commitments & Staged Replies</h3>
          </div>
          <div className="space-y-2">
            {brief.commitments_detected.map((item) => (
              <div
                key={item.item_id}
                className="flex items-center justify-between rounded-lg border border-zinc-800/80 bg-zinc-900/40 p-3.5"
              >
                <div className="space-y-1 pr-4">
                  <p className="text-xs font-medium text-zinc-200">{item.summary}</p>
                  <div className="flex items-center gap-2 text-[11px] text-zinc-500 font-mono">
                    <span>Target: {item.counterparty}</span>
                    <span>•</span>
                    <span>Confidence: {(item.confidence * 100).toFixed(0)}%</span>
                  </div>
                </div>
                {item.staged_reply_action_id && (
                  <button
                    onClick={() => onApproveAction(item.staged_reply_action_id!)}
                    className="flex shrink-0 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 transition shadow-sm"
                  >
                    <Check className="h-3.5 w-3.5" />
                    Approve Staged Reply
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Schedule Conflicts */}
      {brief.schedule_conflicts.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Calendar className="h-4 w-4 text-rose-400" />
            <h3 className="text-sm font-semibold text-zinc-200">Calendar Conflicts Detected</h3>
          </div>
          <div className="space-y-2">
            {brief.schedule_conflicts.map((conf, i) => (
              <div key={i} className="rounded-lg border border-rose-950/60 bg-rose-950/20 p-3.5">
                <div className="flex items-center gap-2 text-xs font-semibold text-rose-300">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  <span>Overlap: '{conf.event_a_title}' vs '{conf.event_b_title}'</span>
                </div>
                <p className="mt-1 text-xs text-zinc-400 font-mono">
                  ↳ Recommended Fix: {conf.recommended_resolution}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Signal-to-Trajectory Tech Radar */}
      {brief.tech_radar.length > 0 && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-purple-400" />
            <h3 className="text-sm font-semibold text-zinc-200">Signal-to-Trajectory Technical Radar</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {brief.tech_radar.map((item, i) => (
              <a
                key={i}
                href={item.url}
                target="_blank"
                rel="noreferrer"
                className="group block rounded-lg border border-zinc-800/80 bg-zinc-900/30 p-3 hover:border-purple-500/40 hover:bg-zinc-900/60 transition"
              >
                <div className="flex items-start justify-between">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-purple-400 bg-purple-500/10 px-2 py-0.5 rounded">
                    {item.source} • {item.trajectory_tag}
                  </span>
                  <ExternalLink className="h-3.5 w-3.5 text-zinc-500 group-hover:text-purple-400 transition" />
                </div>
                <h4 className="mt-2 text-xs font-semibold text-zinc-200 group-hover:text-white line-clamp-1">
                  {item.title}
                </h4>
                <p className="mt-1 text-[11px] text-zinc-400 leading-snug">
                  {item.relevance_rationale}
                </p>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
