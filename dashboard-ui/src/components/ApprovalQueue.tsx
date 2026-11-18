import React, { useState } from 'react';
import { ActionEnvelope, ApprovalStatus } from '../types/schema';
import { ShieldAlert, CheckCircle, XCircle, Edit3, Lock } from 'lucide-react';

interface ApprovalQueueProps {
  actions: ActionEnvelope[];
  onResolve: (actionId: string, status: ApprovalStatus, editedPayload?: Record<string, unknown>) => Promise<void>;
}

export const ApprovalQueue: React.FC<ApprovalQueueProps> = ({ actions, onResolve }) => {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editedText, setEditedText] = useState<string>('');

  const handleStartEdit = (action: ActionEnvelope) => {
    setEditingId(action.action_id);
    const bodyText = (action.payload.body || action.payload.message || action.payload.text || '') as string;
    setEditedText(bodyText);
  };

  const handleSaveEditAndApprove = async (action: ActionEnvelope) => {
    const updatedPayload = { ...action.payload };
    if ('body' in updatedPayload) updatedPayload.body = editedText;
    else if ('message' in updatedPayload) updatedPayload.message = editedText;
    else updatedPayload.text = editedText;

    await onResolve(action.action_id, 'EDITED', updatedPayload);
    setEditingId(null);
  };

  if (!actions.length) {
    return (
      <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-6 text-center">
        <CheckCircle className="mx-auto h-8 w-8 text-emerald-500 mb-2 opacity-80" />
        <p className="text-zinc-400 text-sm">All Zero-Trust guardrails clear. Zero pending Tier-2 approvals.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-amber-400" />
          <h2 className="text-base font-semibold text-zinc-100">Zero-Trust Approval Enclave (Tier 2)</h2>
        </div>
        <span className="rounded-full bg-amber-500/10 px-2.5 py-0.5 text-xs font-medium text-amber-400 border border-amber-500/20">
          {actions.length} Pending Signoff
        </span>
      </div>

      <div className="space-y-3">
        {actions.map((act) => {
          const isEditing = editingId === act.action_id;
          const riskColor = act.risk_score >= 0.8 ? 'text-rose-400 bg-rose-500/10 border-rose-500/20' : 'text-amber-400 bg-amber-500/10 border-amber-500/20';

          return (
            <div key={act.action_id} className="rounded-xl border border-zinc-800 bg-zinc-900/70 p-5 shadow-lg">
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-zinc-300 bg-zinc-800 px-2 py-0.5 rounded">
                      {act.tool_name}
                    </span>
                    <span className={`text-xs px-2 py-0.5 rounded border font-mono ${riskColor}`}>
                      Risk Score: {(act.risk_score * 100).toFixed(0)}%
                    </span>
                    <span className="text-xs text-zinc-500 font-mono">Agent: {act.agent_id}</span>
                  </div>
                  <h3 className="mt-2 text-sm font-medium text-zinc-200">
                    {act.diff_preview?.affected_resource || `Action on ${act.tool_name}`}
                  </h3>
                </div>

                <div className="flex items-center gap-1.5">
                  <Lock className="h-4 w-4 text-zinc-500" />
                </div>
              </div>

              {/* Risk Factors Audit Trail */}
              <div className="mt-3 flex flex-wrap gap-1.5">
                {act.risk_factors.map((f, i) => (
                  <span key={i} className="text-[11px] text-zinc-400 bg-zinc-800/80 px-2 py-0.5 rounded-md">
                    • {f}
                  </span>
                ))}
              </div>

              {/* Diff Preview / Staged Content */}
              <div className="mt-4 rounded-lg bg-zinc-950 p-3 border border-zinc-800/80">
                {isEditing ? (
                  <div className="space-y-2">
                    <label className="text-xs text-zinc-400 font-medium">Edit Staged Text (Triggers Edit-Delta Learning):</label>
                    <textarea
                      value={editedText}
                      onChange={(e) => setEditedText(e.target.value)}
                      rows={4}
                      className="w-full rounded-md bg-zinc-900 border border-zinc-700 p-2 text-xs text-zinc-200 focus:outline-none focus:ring-1 focus:ring-emerald-500 font-mono"
                    />
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => setEditingId(null)}
                        className="px-2.5 py-1 text-xs text-zinc-400 hover:text-zinc-200"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => handleSaveEditAndApprove(act)}
                        className="px-3 py-1 rounded bg-emerald-600 text-xs font-medium text-white hover:bg-emerald-500"
                      >
                        Save & Approve
                      </button>
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="text-[11px] font-mono uppercase tracking-wider text-zinc-500 mb-1">Staged Payload Preview:</div>
                    <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap">
                      {act.diff_preview?.proposed || JSON.stringify(act.payload, null, 2)}
                    </pre>
                  </div>
                )}
              </div>

              {/* Action Buttons */}
              {!isEditing && (
                <div className="mt-4 flex items-center justify-end gap-2">
                  <button
                    onClick={() => handleStartEdit(act)}
                    className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800/60 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-700 transition"
                  >
                    <Edit3 className="h-3.5 w-3.5" />
                    Edit & Refine
                  </button>
                  <button
                    onClick={() => onResolve(act.action_id, 'REJECTED')}
                    className="flex items-center gap-1.5 rounded-lg border border-rose-900/50 bg-rose-950/40 px-3 py-1.5 text-xs font-medium text-rose-300 hover:bg-rose-900/60 transition"
                  >
                    <XCircle className="h-3.5 w-3.5" />
                    Reject
                  </button>
                  <button
                    onClick={() => onResolve(act.action_id, 'APPROVED')}
                    className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 shadow-sm transition"
                  >
                    <CheckCircle className="h-3.5 w-3.5" />
                    Sign & Execute
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
