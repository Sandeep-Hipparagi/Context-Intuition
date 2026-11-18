import React from 'react';
import { WsConnectionState } from '../api/client';
import { Cpu, Chrome, Settings as SettingsIcon, AlertCircle } from 'lucide-react';

interface TelemetryBarProps {
  wsState: WsConnectionState;
  pendingCount: number;
  onOpenSettings: () => void;
}

export const TelemetryBar: React.FC<TelemetryBarProps> = ({ wsState, pendingCount, onOpenSettings }) => {
  return (
    <header className="border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-30 px-6 py-3.5">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        {/* Brand */}
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center h-9 w-9 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-lg">
            🧠
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-bold text-base tracking-tight text-zinc-100">VibeCore</span>
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 border border-zinc-700">
                v0.1.0-alpha
              </span>
            </div>
            <p className="text-[11px] text-zinc-500 font-mono">Local-First Multi-Agent Runtime</p>
          </div>
        </div>

        {/* Telemetry Pills */}
        <div className="flex items-center gap-3">
          {/* Orchestrator WS State */}
          <div className="flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-xs font-mono">
            <span
              className={`h-2 w-2 rounded-full ${
                wsState === 'CONNECTED'
                  ? 'bg-emerald-500 animate-pulse'
                  : wsState === 'AUTH_ERROR'
                  ? 'bg-amber-500'
                  : 'bg-rose-500'
              }`}
            />
            <span className="text-zinc-400">Hub:</span>
            <span
              className={
                wsState === 'CONNECTED'
                  ? 'text-emerald-400 font-semibold'
                  : wsState === 'AUTH_ERROR'
                  ? 'text-amber-400 font-semibold'
                  : 'text-rose-400 font-semibold'
              }
            >
              {wsState === 'CONNECTED' ? 'Online' : wsState === 'AUTH_ERROR' ? 'Auth Error' : 'Offline'}
            </span>
          </div>

          {/* Local LLM Runtime Target */}
          <div className="hidden sm:flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-xs font-mono">
            <Cpu className="h-3.5 w-3.5 text-blue-400" />
            <span className="text-zinc-400">Inference:</span>
            <span className="text-zinc-200">Local (Ollama/vLLM)</span>
          </div>

          {/* Chrome Extension Status */}
          <div className="hidden md:flex items-center gap-2 rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-1.5 text-xs font-mono">
            <Chrome className="h-3.5 w-3.5 text-purple-400" />
            <span className="text-zinc-400">Bridge:</span>
            <span className="text-zinc-200">MV3 Active</span>
          </div>

          {/* Pending Tier-2 Badge */}
          {pendingCount > 0 && (
            <div className="flex items-center gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-1.5 text-xs font-mono text-amber-400 font-semibold animate-pulse">
              <AlertCircle className="h-3.5 w-3.5" />
              <span>{pendingCount} Pending Signoff</span>
            </div>
          )}

          {/* Settings Trigger */}
          <button
            onClick={onOpenSettings}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 transition"
            title="Settings & Bridge Token"
          >
            <SettingsIcon className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Settings</span>
          </button>
        </div>
      </div>
    </header>
  );
};
