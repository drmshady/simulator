import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { STUDIO_EVENT_TYPES, StudioEventStream, type EventSourceLike } from '../src/api/events';

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  readyState = 0;
  closed = false;
  private readonly listeners = new Map<string, ((event: Event) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.closed = true;
    this.readyState = 2;
  }

  emit(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }

  open(): void {
    this.readyState = 1;
    this.emit('open', new Event('open'));
  }

  /** The browser is retrying by itself. */
  drop(): void {
    this.readyState = 0;
    this.emit('error', new Event('error'));
  }

  /** The browser has given up. */
  fail(): void {
    this.readyState = 2;
    this.emit('error', new Event('error'));
  }

  send(type: string, data: string, id = ''): void {
    this.emit(type, new MessageEvent(type, { data, lastEventId: id }));
  }
}

function latest(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error('no EventSource was created');
  return source;
}

function makeStream() {
  return new StudioEventStream({
    url: '/api/events',
    createEventSource: (url) => new FakeEventSource(url),
    initialRetryMs: 1_000,
    maxRetryMs: 8_000,
  });
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('StudioEventStream', () => {
  it('connects only when started and listens for every event type', () => {
    const stream = makeStream();
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(stream.getState()).toBe('closed');
    stream.start();
    expect(latest().url).toBe('/api/events');
    expect(stream.getState()).toBe('connecting');
    latest().open();
    expect(stream.getState()).toBe('open');
    expect(STUDIO_EVENT_TYPES).toContain('run.progress');
    expect(STUDIO_EVENT_TYPES).toHaveLength(9);
    stream.stop();
  });

  it('delivers parsed data to subscribers of that type only', () => {
    const stream = makeStream();
    const progress = vi.fn();
    const finished = vi.fn();
    stream.subscribe('run.progress', progress);
    stream.subscribe('run.finished', finished);
    stream.start();
    latest().open();
    latest().send('run.progress', JSON.stringify({ run_id: 'r1', percent: 40, stage: 'solve', remaining_s: 300 }), '17');
    expect(progress).toHaveBeenCalledWith(
      { run_id: 'r1', percent: 40, stage: 'solve', remaining_s: 300 },
      { type: 'run.progress', id: '17' },
    );
    expect(finished).not.toHaveBeenCalled();
    expect(stream.lastEventId).toBe('17');
    stream.stop();
  });

  it('stops delivering after unsubscribe, and one failing handler does not stop the others', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const stream = makeStream();
    const failing = vi.fn(() => {
      throw new Error('boom');
    });
    const other = vi.fn();
    const unsubscribe = stream.subscribe('job.failed', failing);
    stream.subscribe('job.failed', other);
    stream.start();
    latest().send('job.failed', '{"job_id":"j1"}');
    expect(failing).toHaveBeenCalledTimes(1);
    expect(other).toHaveBeenCalledTimes(1);
    unsubscribe();
    latest().send('job.failed', '{"job_id":"j2"}');
    expect(failing).toHaveBeenCalledTimes(1);
    expect(other).toHaveBeenCalledTimes(2);
    stream.stop();
  });

  it('ignores data that is not JSON', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    const stream = makeStream();
    const handler = vi.fn();
    stream.subscribe('preview.ready', handler);
    stream.start();
    latest().send('preview.ready', 'not json');
    expect(handler).not.toHaveBeenCalled();
    stream.stop();
  });

  it('runs connect listeners on every (re)connect, including the browser reconnecting by itself', () => {
    const stream = makeStream();
    const onConnect = vi.fn();
    stream.onConnect(onConnect);
    stream.start();
    latest().open();
    expect(onConnect).toHaveBeenCalledTimes(1);
    latest().drop();
    expect(stream.getState()).toBe('connecting');
    expect(FakeEventSource.instances).toHaveLength(1);
    latest().open();
    expect(onConnect).toHaveBeenCalledTimes(2);
    stream.stop();
  });

  it('opens a new EventSource with a growing wait when the browser gives up, and resets the wait after opening', () => {
    const stream = makeStream();
    const onConnect = vi.fn();
    stream.onConnect(onConnect);
    stream.start();
    const first = latest();
    first.fail();
    expect(first.closed).toBe(true);
    expect(stream.getState()).toBe('closed');

    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(stream.getState()).toBe('connecting');

    latest().fail();
    vi.advanceTimersByTime(1_999);
    expect(FakeEventSource.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(3);

    latest().fail();
    vi.advanceTimersByTime(4_000);
    latest().fail();
    vi.advanceTimersByTime(8_000);
    latest().fail();
    vi.advanceTimersByTime(8_000); // capped at maxRetryMs
    expect(FakeEventSource.instances).toHaveLength(6);

    latest().open();
    expect(onConnect).toHaveBeenCalledTimes(1);
    latest().fail();
    vi.advanceTimersByTime(1_000); // back to the first wait
    expect(FakeEventSource.instances).toHaveLength(7);
    stream.stop();
  });

  it('ignores events from a replaced EventSource', () => {
    const stream = makeStream();
    const handler = vi.fn();
    stream.subscribe('run.finished', handler);
    stream.start();
    const first = latest();
    first.fail();
    vi.advanceTimersByTime(1_000);
    first.send('run.finished', '{"run_id":"old"}');
    expect(handler).not.toHaveBeenCalled();
    latest().send('run.finished', '{"run_id":"new"}');
    expect(handler).toHaveBeenCalledTimes(1);
    stream.stop();
  });

  it('stop closes the connection and cancels a pending reconnect', () => {
    const stream = makeStream();
    stream.start();
    latest().fail();
    stream.stop();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(stream.getState()).toBe('closed');

    stream.start();
    const second = latest();
    expect(FakeEventSource.instances).toHaveLength(2);
    stream.stop();
    expect(second.closed).toBe(true);
  });

  it('reconnectNow skips the wait while a reconnect is pending, and does nothing while connected', () => {
    const stream = makeStream();
    stream.start();
    latest().open();
    stream.reconnectNow();
    expect(FakeEventSource.instances).toHaveLength(1);
    latest().fail();
    stream.reconnectNow();
    expect(FakeEventSource.instances).toHaveLength(2);
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(2);
    stream.stop();
  });

  it('notifies state listeners on changes only', () => {
    const stream = makeStream();
    const listener = vi.fn();
    const unsubscribe = stream.subscribeState(listener);
    stream.start();
    latest().open();
    latest().open();
    expect(listener).toHaveBeenCalledTimes(2); // connecting, open
    unsubscribe();
    stream.stop();
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it('retries when the EventSource cannot be created', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    let attempts = 0;
    const stream = new StudioEventStream({
      createEventSource: (url) => {
        attempts += 1;
        if (attempts === 1) throw new Error('not supported');
        return new FakeEventSource(url);
      },
      initialRetryMs: 500,
    });
    stream.start();
    expect(stream.getState()).toBe('closed');
    vi.advanceTimersByTime(500);
    expect(attempts).toBe(2);
    expect(stream.getState()).toBe('connecting');
    stream.stop();
  });
});
