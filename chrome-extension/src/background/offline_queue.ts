/**
 * IndexedDB Offline Write-Ahead Queue for VibeCore Chrome Extension.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Persistent ring buffer (max 1000 entries, FIFO eviction) for telemetry frames
 * when the WebSocket bridge to the orchestrator is disconnected.
 */

import { BridgeMessage, BridgeMessageType } from '../types/bridge';

const DB_NAME = 'vibecore_offline_queue';
const STORE_NAME = 'frames';
const DB_VERSION = 1;
const MAX_ENTRIES = 1000;

export interface QueuedFrame {
  id?: number;
  frame_id: string;
  message: BridgeMessage;
  captured_at: string;
  retry_count: number;
  last_attempt?: string;
}

let dbPromise: Promise<IDBDatabase> | null = null;

function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'id', autoIncrement: true });
        store.createIndex('frame_id', 'frame_id', { unique: true });
        store.createIndex('captured_at', 'captured_at', { unique: false });
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });

  return dbPromise;
}

export async function enqueueFrame(message: BridgeMessage): Promise<void> {
  const db = await openDB();
  const frame: QueuedFrame = {
    frame_id: message.message_id,
    message,
    captured_at: new Date().toISOString(),
    retry_count: 0,
  };

  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const request = store.add(frame);

    request.onsuccess = async () => {
      await enforceMaxEntries(db);
      resolve();
    };
    request.onerror = () => reject(request.error);
  });
}

async function enforceMaxEntries(db: IDBDatabase): Promise<void> {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const index = store.index('captured_at');
    const countRequest = store.count();

    countRequest.onsuccess = () => {
      const count = countRequest.result;
      if (count > MAX_ENTRIES) {
        const excess = count - MAX_ENTRIES;
        const cursorRequest = index.openCursor();
        let deleted = 0;

        cursorRequest.onsuccess = (event) => {
          const cursor = (event.target as IDBRequest<IDBCursorWithValue>).result;
          if (cursor && deleted < excess) {
            cursor.delete();
            deleted++;
            cursor.continue();
          } else {
            resolve();
          }
        };
        cursorRequest.onerror = () => reject(cursorRequest.error);
      } else {
        resolve();
      }
    };
    countRequest.onerror = () => reject(countRequest.error);
  });
}

export async function getAllFrames(): Promise<QueuedFrame[]> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const store = tx.objectStore(STORE_NAME);
    const request = store.getAll();

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function deleteFrame(id: number): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const request = store.delete(id);

    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function updateFrameRetry(id: number, retryCount: number): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const getRequest = store.get(id);

    getRequest.onsuccess = () => {
      const frame = getRequest.result;
      if (frame) {
        frame.retry_count = retryCount;
        frame.last_attempt = new Date().toISOString();
        const putRequest = store.put(frame);
        putRequest.onsuccess = () => resolve();
        putRequest.onerror = () => reject(putRequest.error);
      } else {
        resolve();
      }
    };
    getRequest.onerror = () => reject(getRequest.error);
  });
}

export async function clearQueue(): Promise<void> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite');
    const store = tx.objectStore(STORE_NAME);
    const request = store.clear();

    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getQueueSize(): Promise<number> {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readonly');
    const store = tx.objectStore(STORE_NAME);
    const request = store.count();

    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}