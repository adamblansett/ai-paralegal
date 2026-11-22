import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

export function useMatterEvents(
  matterId: string | null,
  onRefresh: () => Promise<void>
): { connected: boolean; liveActivity: string | null } {
  const [connected, setConnected] = useState(false);
  const [liveActivity, setLiveActivity] = useState<string | null>(null);
  const refreshTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!matterId || typeof window === 'undefined' || typeof EventSource === 'undefined') {
      setConnected(false);
      setLiveActivity(null);
      return;
    }

    const scheduleRefresh = () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
      refreshTimerRef.current = window.setTimeout(() => {
        void onRefresh();
      }, 250);
    };

    const source = new EventSource(api.matterEventsUrl(matterId));

    source.addEventListener('snapshot', () => {
      setConnected(true);
    });

    source.addEventListener('document_status', (evt) => {
      try {
        const data = JSON.parse((evt as MessageEvent<string>).data) as {
          filename?: string;
          ingestion_status?: string;
        };
        if (data.filename && data.ingestion_status) {
          setLiveActivity(`${data.filename}: ${data.ingestion_status}`);
        }
      } catch {
        // ignore malformed payload
      }
      scheduleRefresh();
    });

    source.addEventListener('task_progress', (evt) => {
      try {
        const data = JSON.parse((evt as MessageEvent<string>).data) as {
          message?: string;
          task_type?: string;
        };
        setLiveActivity(data.message || `${data.task_type || 'Task'} running...`);
      } catch {
        // ignore
      }
    });

    source.addEventListener('batch_matrix_progress', (evt) => {
      try {
        const data = JSON.parse((evt as MessageEvent<string>).data) as {
          completed_docs?: number;
          total_docs?: number;
          current_document?: string;
        };
        setLiveActivity(
          `Batch review (${data.completed_docs ?? 0}/${data.total_docs ?? 0}): ${
            data.current_document ?? ''
          }`
        );
      } catch {
        // ignore
      }
    });

    source.addEventListener('task_completed', (evt) => {
      try {
        const data = JSON.parse((evt as MessageEvent<string>).data) as {
          task_type?: string;
        };
        setLiveActivity(`${(data.task_type || 'Analysis').replace(/_/g, ' ')} completed`);
      } catch {
        // ignore
      }
      scheduleRefresh();
    });

    source.addEventListener('task_failed', () => {
      setLiveActivity('Background task failed');
      scheduleRefresh();
    });

    source.onerror = () => {
      setConnected(false);
    };

    return () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
      source.close();
      setConnected(false);
    };
  }, [matterId, onRefresh]);

  return { connected, liveActivity };
}
