import React, { useState, useEffect } from 'react';
import { getStoredToken, setStoredToken, fetchAuthToken } from '../api/client';
import { Key, X, Check, RefreshCw } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaveToken: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose, onSaveToken }) => {
  const [token, setToken] = useState<string>('');
  const [feedback, setFeedback] = useState<string>('');

  useEffect(() => {
    if (isOpen) {
      setToken(getStoredToken());
      setFeedback('');
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = () => {
    setStoredToken(token);
    setFeedback('Token saved! Reconnecting telemetry bridge...');
    setTimeout(() => {
      onSaveToken();
      onClose();
    }, 1000);
  };

  const handleAutoDiscover = async () => {
    try {
      setFeedback('Querying local orchestrator...');
      const data = await fetchAuthToken();
      if (data.token) {
        setToken(data.token);
        setFeedback('Found active local bridge token!');
      }
    } catch (e) {
      setFeedback('Orchestrator offline or endpoint inaccessible.');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-950 p-6 shadow-2xl space-y-5">
        <div className="flex items-center justify-between border-b border-zinc-800/80 pb-3">
          <div className="flex items-center gap-2.5">
            <Key className="h-5 w-5 text-emerald-400" />
            <h3 className="text-sm font-bold text-zinc-100">VibeCore Bridge Authentication</h3>
          </div>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3">
          <label className="text-xs font-medium text-zinc-300">
            Handshake Bridge Token (~/.vibecore/bridge.token):
          </label>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Paste local handshake token..."
            className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-200 font-mono focus:border-emerald-500 focus:outline-none"
          />
          <div className="flex items-center justify-between">
            <button
              onClick={handleAutoDiscover}
              className="flex items-center gap-1.5 text-xs text-emerald-400 hover:text-emerald-300 font-medium"
            >
              <RefreshCw className="h-3 w-3" />
              Auto-Discover Local Token
            </button>
            {feedback && <span className="text-xs text-zinc-400 font-mono">{feedback}</span>}
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-zinc-800/80 pt-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-zinc-800 px-4 py-1.5 text-xs font-medium text-zinc-400 hover:bg-zinc-900"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="flex items-center gap-1.5 rounded-lg bg-emerald-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-emerald-500 shadow-sm"
          >
            <Check className="h-3.5 w-3.5" />
            Save & Connect
          </button>
        </div>
      </div>
    </div>
  );
};
