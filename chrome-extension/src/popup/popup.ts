/**
 * Popup Script for VibeCore Chrome Extension.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 */

document.addEventListener('DOMContentLoaded', async () => {
  const statusPill = document.getElementById('status-pill') as HTMLElement;
  const tokenInput = document.getElementById('token-input') as HTMLInputElement;
  const saveTokenBtn = document.getElementById('save-token-btn') as HTMLButtonElement;
  const tokenFeedback = document.getElementById('token-feedback') as HTMLElement;
  const actionFeedback = document.getElementById('action-feedback') as HTMLElement;
  const reconnectBtn = document.getElementById('reconnect-btn') as HTMLButtonElement;
  const scanTabBtn = document.getElementById('scan-tab-btn') as HTMLButtonElement;

  const toggleWhatsapp = document.getElementById('toggle-whatsapp') as HTMLInputElement;
  const toggleGrocery = document.getElementById('toggle-grocery') as HTMLInputElement;
  const toggleInstagram = document.getElementById('toggle-instagram') as HTMLInputElement;

  // 1. Load saved preferences from chrome.storage.local
  chrome.storage.local.get(['vibecore_token', 'workflows'], (result) => {
    if (result.vibecore_token) {
      tokenInput.value = result.vibecore_token;
    }
    if (result.workflows) {
      toggleWhatsapp.checked = result.workflows.whatsapp_copilot !== false;
      toggleGrocery.checked = result.workflows.grocery_tracker !== false;
      toggleInstagram.checked = result.workflows.instagram_archivist !== false;
    }
  });

  // 2. Query live connection status from background service worker
  function updateConnectionStatus(): void {
    chrome.runtime.sendMessage({ source: 'vibecore-popup', type: 'GET_STATUS' }, (res) => {
      if (chrome.runtime.lastError || !res) {
        setStatusUI('DISCONNECTED');
        return;
      }
      setStatusUI(res.status || 'DISCONNECTED');
    });
  }

  function setStatusUI(status: 'CONNECTED' | 'DISCONNECTED' | 'AUTH_ERROR'): void {
    statusPill.className = 'pill';
    if (status === 'CONNECTED') {
      statusPill.classList.add('connected');
      statusPill.textContent = 'Connected';
    } else if (status === 'AUTH_ERROR') {
      statusPill.classList.add('auth-error');
      statusPill.textContent = 'Auth Error';
    } else {
      statusPill.classList.add('disconnected');
      statusPill.textContent = 'Disconnected';
    }
  }

  updateConnectionStatus();

  // 3. Save bridge auth token
  saveTokenBtn.addEventListener('click', () => {
    const token = tokenInput.value.trim();
    chrome.storage.local.set({ vibecore_token: token }, () => {
      tokenFeedback.textContent = 'Token saved. Reconnecting...';
      tokenFeedback.style.color = '#34d399';
      setTimeout(() => (tokenFeedback.textContent = ''), 3000);

      // Trigger reconnect in background service worker
      chrome.runtime.sendMessage({ source: 'vibecore-popup', type: 'RECONNECT' }, () => {
        setTimeout(updateConnectionStatus, 1000);
      });
    });
  });

  // 4. Save workflow toggle state
  function saveWorkflowToggles(): void {
    const workflows = {
      whatsapp_copilot: toggleWhatsapp.checked,
      grocery_tracker: toggleGrocery.checked,
      instagram_archivist: toggleInstagram.checked,
    };
    chrome.storage.local.set({ workflows }, () => {
      actionFeedback.textContent = 'Workflow settings updated.';
      actionFeedback.style.color = '#34d399';
      setTimeout(() => (actionFeedback.textContent = ''), 2000);
    });
  }

  toggleWhatsapp.addEventListener('change', saveWorkflowToggles);
  toggleGrocery.addEventListener('change', saveWorkflowToggles);
  toggleInstagram.addEventListener('change', saveWorkflowToggles);

  // 5. Manual reconnect button
  reconnectBtn.addEventListener('click', () => {
    actionFeedback.textContent = 'Attempting connection...';
    chrome.runtime.sendMessage({ source: 'vibecore-popup', type: 'RECONNECT' }, () => {
      setTimeout(updateConnectionStatus, 1500);
    });
  });

  // 6. Manual scan current tab button
  scanTabBtn.addEventListener('click', async () => {
    actionFeedback.textContent = 'Scanning tab...';
    actionFeedback.style.color = '#a1a1aa';

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      actionFeedback.textContent = 'No active tab found.';
      actionFeedback.style.color = '#f87171';
      return;
    }

    try {
      const response = await chrome.tabs.sendMessage(tab.id, {
        type: 'EXECUTE_DOM_ACTION',
        command: 'SCAN_CURRENT_TAB'
      });

      if (response?.success) {
        actionFeedback.textContent = response.message;
        actionFeedback.style.color = '#34d399';
      } else {
        actionFeedback.textContent = response?.message || 'No supported content found.';
        actionFeedback.style.color = '#fbbf24';
      }
    } catch (err: unknown) {
      actionFeedback.textContent = 'Tab not initialized. Reload page.';
      actionFeedback.style.color = '#f87171';
    }
  });
});
