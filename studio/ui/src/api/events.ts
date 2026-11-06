import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useSyncExternalStore } from 'react';

import type { StudioEventDataMap, StudioEventType } from './types';

const EVENT_TYPES = {
  'preview.ready': true,
  'run.progress': true,
  'run.finished': true,
  'study.progress': true,
  'job.failed': true,
  'validity.changed': true,
  'capability.changed': true,
} satisfies Record<StudioEventType, true>;

export const STUDIO_EVENT_TYPES = Object.keys(EVENT_TYPES) as StudioEventType[];

export type ConnectionState = 'connecting' | 'open' | 'closed';

/** The part of the browser's EventSource used here, so tests can pass a fake. */
export interface EventSourceLike {
  readonly readyState: number;
  addEventListener(type: string, listener: (event: Event) => void): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export interface StudioEventMeta {
  type: StudioEventType;
  id: string;
}

export type StudioEventHandler<K extends StudioEventType> = (data: StudioEventDataMap[K], meta: StudioEventMeta) => void;

export type StudioEventHandlers = { [K in StudioEventType]?: StudioEventHandler<K> };

export interface EventStreamOptions {
  url?: string;
  createEventSource?: EventSourceFactory;
  /** First wait before a manual reconnect, doubled after each failure up to `maxRetryMs`. */
  initialRetryMs?: number;
  maxRetryMs?: number;
}

type AnyHandler = (data: unknown, meta: StudioEventMeta) => void;

// EventSource.readyState values.
const CLOSED = 2;

const defaultFactory: EventSourceFactory = (url) => new EventSource(url);

/**
 * One connection to GET /api/events shared by the whole interface. The browser's EventSource reconnects by itself
 * (sending Last-Event-ID so the server can replay what was missed); when it gives up (readyState CLOSED, e.g. the
 * server was down or answered 401) this opens a new one after a growing wait. Connect listeners run on every
 * (re)connect so the caller can refetch everything that may have changed while offline.
 */
export class StudioEventStream {
  private readonly url: string;
  private readonly createEventSource: EventSourceFactory;
  private readonly initialRetryMs: number;
  private readonly maxRetryMs: number;
  private readonly handlers = new Map<StudioEventType, Set<AnyHandler>>();
  private readonly stateListeners = new Set<() => void>();
  private readonly connectListeners = new Set<() => void>();
  private source: EventSourceLike | null = null;
  private state: ConnectionState = 'closed';
  private running = false;
  private retryMs: number;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  /** Id of the last event received, for diagnostics. */
  lastEventId = '';

  constructor(options: EventStreamOptions = {}) {
    this.url = options.url ?? '/api/events';
    this.createEventSource = options.createEventSource ?? defaultFactory;
    this.initialRetryMs = options.initialRetryMs ?? 1_000;
    this.maxRetryMs = options.maxRetryMs ?? 30_000;
    this.retryMs = this.initialRetryMs;
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    this.retryMs = this.initialRetryMs;
    if (typeof window !== 'undefined') window.addEventListener('online', this.reconnectNow);
    this.connect();
  }

  stop(): void {
    this.running = false;
    this.clearRetryTimer();
    if (typeof window !== 'undefined') window.removeEventListener('online', this.reconnectNow);
    this.source?.close();
    this.source = null;
    this.setState('closed');
  }

  /** Reconnects at once if the stream is waiting to retry (for example when the laptop is back online). */
  reconnectNow = (): void => {
    if (this.running && this.source === null) this.connect();
  };

  subscribe<K extends StudioEventType>(type: K, handler: StudioEventHandler<K>): () => void {
    let set = this.handlers.get(type);
    if (!set) {
      set = new Set();
      this.handlers.set(type, set);
    }
    const stored = handler as AnyHandler;
    set.add(stored);
    return () => {
      set.delete(stored);
    };
  }

  /** Called on every successful (re)connect. Returns an unsubscribe function. */
  onConnect(listener: () => void): () => void {
    this.connectListeners.add(listener);
    return () => {
      this.connectListeners.delete(listener);
    };
  }

  getState = (): ConnectionState => this.state;

  subscribeState = (listener: () => void): (() => void) => {
    this.stateListeners.add(listener);
    return () => {
      this.stateListeners.delete(listener);
    };
  };

  private connect(): void {
    this.clearRetryTimer();
    let source: EventSourceLike;
    try {
      source = this.createEventSource(this.url);
    } catch (error) {
      console.error('Could not open the event stream', error);
      this.setState('closed');
      this.scheduleReconnect();
      return;
    }
    this.source = source;
    this.setState('connecting');

    source.addEventListener('open', () => {
      if (this.source !== source) return;
      this.retryMs = this.initialRetryMs;
      this.setState('open');
      for (const listener of [...this.connectListeners]) callSafely(listener);
    });
    source.addEventListener('error', () => {
      if (this.source !== source) return;
      if (source.readyState === CLOSED) {
        // The browser has given up on this one.
        source.close();
        this.source = null;
        this.setState('closed');
        this.scheduleReconnect();
      } else {
        this.setState('connecting');
      }
    });
    for (const type of STUDIO_EVENT_TYPES) {
      source.addEventListener(type, (event) => {
        if (this.source === source) this.dispatch(type, event as MessageEvent);
      });
    }
  }

  private dispatch(type: StudioEventType, event: MessageEvent): void {
    const id = typeof event.lastEventId === 'string' ? event.lastEventId : '';
    if (id) this.lastEventId = id;
    const set = this.handlers.get(type);
    if (!set || set.size === 0) return;
    let data: unknown;
    try {
      data = JSON.parse(String(event.data));
    } catch {
      console.warn(`Ignored a "${type}" event whose data is not JSON`);
      return;
    }
    const meta: StudioEventMeta = { type, id };
    for (const handler of [...set]) callSafely(() => handler(data, meta));
  }

  private scheduleReconnect(): void {
    if (!this.running || this.retryTimer !== null) return;
    const delay = this.retryMs;
    this.retryMs = Math.min(this.retryMs * 2, this.maxRetryMs);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (this.running && this.source === null) this.connect();
    }, delay);
  }

  private clearRetryTimer(): void {
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private setState(state: ConnectionState): void {
    if (state === this.state) return;
    this.state = state;
    for (const listener of [...this.stateListeners]) listener();
  }
}

function callSafely(fn: () => void): void {
  try {
    fn();
  } catch (error) {
    console.error('An event handler failed', error);
  }
}

/** The interface's one event stream. It connects only once `useStudioEventStream(true)` runs (after sign-in). */
export const studioEvents = new StudioEventStream();

export function useEventConnectionState(stream: StudioEventStream = studioEvents): ConnectionState {
  return useSyncExternalStore(stream.subscribeState, stream.getState, stream.getState);
}

/**
 * Starts the stream while `enabled` and refetches every query on each (re)connect, so a page shows the current
 * state of runs again after the connection was lost. Used once, by the signed-in shell.
 */
export function useStudioEventStream(enabled: boolean, stream: StudioEventStream = studioEvents): ConnectionState {
  const queryClient = useQueryClient();
  useEffect(() => {
    if (!enabled) return undefined;
    const removeListener = stream.onConnect(() => {
      void queryClient.invalidateQueries();
    });
    stream.start();
    return () => {
      removeListener();
      stream.stop();
    };
  }, [enabled, queryClient, stream]);
  return useEventConnectionState(stream);
}

/**
 * Subscribes a component to event types, for example `useStudioEvents({'run.progress': (p) => ...})`. The handlers
 * may be a new object on every render. Returns the connection state.
 */
export function useStudioEvents(
  handlers?: StudioEventHandlers,
  stream: StudioEventStream = studioEvents,
): ConnectionState {
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });
  const types = handlers
    ? STUDIO_EVENT_TYPES.filter((type) => handlers[type] !== undefined).join(' ')
    : '';
  useEffect(() => {
    if (!types) return undefined;
    const unsubscribes = (types.split(' ') as StudioEventType[]).map((type) =>
      stream.subscribe(type, (data, meta) => {
        const handler = handlersRef.current?.[type] as AnyHandler | undefined;
        handler?.(data, meta);
      }),
    );
    return () => {
      for (const unsubscribe of unsubscribes) unsubscribe();
    };
  }, [types, stream]);
  return useEventConnectionState(stream);
}
