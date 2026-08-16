'use strict';
// Tests for the in-game chat/log module (issue #32).
//
// game-log.js provides:
//   * LogBuffer      — ordered, de-duplicated, bounded log rows (DOM-free)
//   * formatEvent    — pure rendering of raw events into human-readable text
//   * formatTimestamp
//   * GameLogUI      — thin DOM binding (requires document; not exercised here)
//
// Run with:  node --test tests/
const { test } = require('node:test');
const assert = require('node:assert');

const {
  LogBuffer,
  InlineChatController,
  formatEvent,
  formatTimestamp,
} = require('../game-log.js');

// ── formatEvent ─────────────────────────────────────────────────────────
test('formats join/leave events', () => {
  assert.strictEqual(
    formatEvent({ event: 'join', id: 'p_abc', name: 'Nova' }),
    'Nova joined the game',
  );
  assert.strictEqual(
    formatEvent({ event: 'leave', id: 'p_abc', name: 'Nova' }),
    'Nova left the game',
  );
});

test('formats death events with and without a killer', () => {
  assert.strictEqual(
    formatEvent({ event: 'death', id: 'p_a', name: 'A', killer: 'p_b', killerName: 'B' }),
    'A was destroyed by B',
  );
  assert.strictEqual(
    formatEvent({ event: 'death', id: 'p_a', name: 'A', killer: '' }),
    'A was destroyed',
  );
  assert.strictEqual(
    formatEvent({ event: 'death', id: 'p_a', name: 'A' }),
    'A was destroyed',
  );
});

test('formats my own death from my perspective', () => {
  const ev = { event: 'death', id: 'me', name: 'Me', killer: 'p_b', killerName: 'B' };
  assert.strictEqual(formatEvent(ev, 'me'), 'You were destroyed by B');
  assert.strictEqual(
    formatEvent({ event: 'death', id: 'me', name: 'Me' }, 'me'),
    'You were destroyed',
  );
});

test('formats chat events', () => {
  assert.strictEqual(
    formatEvent({ event: 'chat', id: 'p_a', name: 'A', text: 'hello' }),
    'A: hello',
  );
});

test('formats pickup and announce events', () => {
  assert.strictEqual(
    formatEvent({ event: 'pickup', id: 'p_a', name: 'A', item: 'shield' }),
    'A picked up shield',
  );
  assert.strictEqual(
    formatEvent({ event: 'announce', text: 'Round over' }),
    'Round over',
  );
});

// ── LogBuffer ordering / dedup / bounds ─────────────────────────────────
test('appends local (unsequenced) events in arrival order', () => {
  const buf = new LogBuffer({ maxMessages: 10 });
  assert.deepStrictEqual(buf.push({ event: 'chat', id: 'a', name: 'A', text: 'hi' }), [buf.rows[0]]);
  buf.push({ event: 'chat', id: 'b', name: 'B', text: 'yo' });
  assert.strictEqual(buf.count(), 2);
  assert.strictEqual(buf.rows[0].text, 'A: hi');
  assert.strictEqual(buf.rows[1].text, 'B: yo');
});

test('drops duplicate/stale sequenced events', () => {
  const buf = new LogBuffer({ maxMessages: 10 });
  buf.push({ event: 'join', seq: 1, id: 'a', name: 'A' });
  assert.strictEqual(buf.count(), 1);
  // Duplicate of seq 1 is ignored.
  assert.strictEqual(buf.push({ event: 'death', seq: 1, id: 'b', name: 'B' }), null);
  assert.strictEqual(buf.count(), 1);
});

test('orders out-of-order events once the gap is filled', () => {
  const buf = new LogBuffer({ maxMessages: 10 });
  buf.push({ event: 'death', seq: 3, id: 'b', name: 'B' });   // gap: seqs 1,2 missing
  assert.strictEqual(buf.count(), 0);
  buf.push({ event: 'join', seq: 1, id: 'a', name: 'A' });
  buf.push({ event: 'join', seq: 2, id: 'c', name: 'C' });
  // Draining applies 2 and 3 in order.
  assert.strictEqual(buf.count(), 3);
  assert.deepStrictEqual(
    buf.rows.map((r) => r.seq),
    [1, 2, 3],
  );
});

test('bounded by maxMessages (newest kept)', () => {
  const buf = new LogBuffer({ maxMessages: 3 });
  for (let i = 1; i <= 6; i++) buf.push({ event: 'chat', seq: i, id: 'a', name: 'A', text: String(i) });
  assert.strictEqual(buf.count(), 3);
  assert.deepStrictEqual(buf.rows.map((r) => r.seq), [4, 5, 6]);
});

test('loadHistory applies a bounded replay in seq order', () => {
  const buf = new LogBuffer({ maxMessages: 20 });
  buf.loadHistory([
    { event: 'join', seq: 1, id: 'a', name: 'A' },
    { event: 'death', seq: 3, id: 'b', name: 'B', killer: 'a', killerName: 'A' },
    { event: 'join', seq: 2, id: 'c', name: 'C' },
  ]);
  assert.strictEqual(buf.count(), 3);
  assert.deepStrictEqual(buf.rows.map((r) => r.seq), [1, 2, 3]);
  assert.strictEqual(buf.rows[2].text, 'B was destroyed by A');
  // The next live event continues contiguously after the history.
  buf.push({ event: 'chat', seq: 4, id: 'a', name: 'A', text: 'welcome' });
  assert.strictEqual(buf.count(), 4);
  assert.strictEqual(buf.rows[3].seq, 4);
});

test('setMaxMessages trims to the new bound', () => {
  const buf = new LogBuffer({ maxMessages: 5 });
  for (let i = 1; i <= 5; i++) buf.push({ event: 'chat', seq: i, id: 'a', name: 'A', text: String(i) });
  buf.setMaxMessages(2);
  assert.strictEqual(buf.count(), 2);
  assert.deepStrictEqual(buf.rows.map((r) => r.seq), [4, 5]);
});

test('clear resets rows and sequence tracking', () => {
  const buf = new LogBuffer({ maxMessages: 10 });
  buf.push({ event: 'join', seq: 1, id: 'a', name: 'A' });
  buf.clear();
  assert.strictEqual(buf.count(), 0);
  // A new server session can start over from seq 1.
  buf.push({ event: 'join', seq: 1, id: 'a', name: 'A' });
  assert.strictEqual(buf.count(), 1);
});

test('formatTimestamp renders HH:MM:SS and tolerates bad input', () => {
  assert.match(formatTimestamp(new Date(0).getTime()), /^\d\d:\d\d:\d\d$/);
  // null/undefined means "no timestamp" and falls back to the current time.
  assert.match(formatTimestamp(null), /^\d\d:\d\d:\d\d$/);
  assert.strictEqual(formatTimestamp('not-a-date'), '');
});

test('invalid sequence values remain local events', () => {
  const buf = new LogBuffer({ maxMessages: 10 });
  buf.push({ event: 'chat', seq: 'not-a-sequence', id: 'a', name: 'A', text: 'hello' });
  buf.push({ event: 'chat', seq: 1.5, id: 'a', name: 'A', text: 'world' });
  assert.strictEqual(buf.count(), 2);
  assert.deepStrictEqual(buf.rows.map((row) => row.seq), [null, null]);
});

test('history can start at a retained sequence after older events expire', () => {
  const buf = new LogBuffer({ maxMessages: 10 });
  buf.loadHistory([
    { event: 'join', seq: 101, id: 'a', name: 'A' },
    { event: 'chat', seq: 102, id: 'a', name: 'A', text: 'hello' },
  ]);
  buf.push({ event: 'chat', seq: 103, id: 'a', name: 'A', text: 'world' });
  assert.deepStrictEqual(buf.rows.map((row) => row.seq), [101, 102, 103]);
});

// ── InlineChatController (auto-hide / fade, issue #37) ─────────────────
// A minimal fake timer so the fade/inactivity state machine can be tested
// deterministically without real wall-clock time.
function makeFakeTimers() {
  let now = 0;
  const tasks = new Map();
  let nextId = 1;
  return {
    now,
    setTimeout(fn, ms) {
      const id = nextId++;
      tasks.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimeout(id) { tasks.delete(id); },
    pending() { return tasks.size; },
    advance(ms) {
      const end = now + ms;
      // Run timers in due-time order; a timer may schedule further timers.
      for (;;) {
        const due = [...tasks.entries()]
          .filter(([, t]) => t.at <= end)
          .sort((a, b) => a[1].at - b[1].at);
        if (!due.length) break;
        const [id, t] = due[0];
        tasks.delete(id);
        now = t.at;
        t.fn();
      }
      now = end;
    },
  };
}

function makeController(timers, opts) {
  opts = opts || {};
  return new InlineChatController({
    autoHide: opts.autoHide,
    inactivityTimeout: opts.inactivityTimeout,
    fadeInMs: opts.fadeInMs,
    fadeOutMs: opts.fadeOutMs,
    setTimeout: timers.setTimeout.bind(timers),
    clearTimeout: timers.clearTimeout.bind(timers),
    onChange: opts.onChange,
  });
}

test('inline chat starts hidden when auto-hide is on and shows on activity', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: true, inactivityTimeout: 5000, fadeInMs: 200, fadeOutMs: 300 });
  assert.strictEqual(ctrl.visible, false);
  assert.strictEqual(ctrl.opacity, 0);
  ctrl.poke();
  assert.strictEqual(ctrl.visible, true);
  assert.strictEqual(ctrl.opacity, 1);
});

test('inline chat fades out after the inactivity timeout', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: true, inactivityTimeout: 5000, fadeInMs: 200, fadeOutMs: 300 });
  ctrl.poke();
  assert.strictEqual(ctrl.visible, true);
  timers.advance(4999);
  assert.strictEqual(ctrl.visible, true, 'still visible just before the timeout');
  timers.advance(1);
  assert.strictEqual(ctrl.opacity, 0, 'opacity drops once the timeout elapses');
  timers.advance(299);
  assert.strictEqual(ctrl.visible, true, 'element lingers during the fade-out');
  timers.advance(1);
  assert.strictEqual(ctrl.visible, false, 'element is removed after the fade-out');
});

test('activity resets the inactivity timer', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: true, inactivityTimeout: 3000, fadeInMs: 100, fadeOutMs: 100 });
  ctrl.poke();
  timers.advance(2000);
  ctrl.poke(); // new message while still visible
  assert.strictEqual(ctrl.visible, true);
  timers.advance(2500);
  assert.strictEqual(ctrl.visible, true, 'timer was reset by the second poke');
  timers.advance(500);
  timers.advance(100); // fade-out duration
  assert.strictEqual(ctrl.visible, false, 'hides after a full inactivity window');
});

test('focus holds the chat visible and blur re-arms the timer', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: true, inactivityTimeout: 3000, fadeInMs: 100, fadeOutMs: 100 });
  ctrl.poke();
  timers.advance(2500);
  ctrl.focus();
  timers.advance(5000);
  assert.strictEqual(ctrl.visible, true, 'no fade-out while typing');
  ctrl.blur();
  timers.advance(2999);
  assert.strictEqual(ctrl.visible, true);
  timers.advance(1);
  timers.advance(100); // fade-out duration
  assert.strictEqual(ctrl.visible, false, 'blur restarts the countdown');
});

test('auto-hide off keeps the chat permanently visible', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: false, inactivityTimeout: 3000, fadeInMs: 100, fadeOutMs: 100 });
  assert.strictEqual(ctrl.visible, true, 'starts visible when permanently shown');
  assert.strictEqual(ctrl.opacity, 1);
  timers.advance(10000);
  assert.strictEqual(ctrl.visible, true, 'never hides while auto-hide is off');
});

test('setAutoHide toggles between permanent and timed visibility', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: true, inactivityTimeout: 2000, fadeInMs: 100, fadeOutMs: 100 });
  ctrl.poke();
  ctrl.setAutoHide(false);
  timers.advance(5000);
  assert.strictEqual(ctrl.visible, true, 'stays visible after disabling auto-hide');
  ctrl.setAutoHide(true);
  assert.strictEqual(ctrl.visible, true, 're-enabling auto-hide starts from visible');
  timers.advance(2000);
  timers.advance(100);
  assert.strictEqual(ctrl.visible, false, 'hides after the window once auto-hide is back on');
});

test('setInactivityTimeout re-arms with the new delay', () => {
  const timers = makeFakeTimers();
  const ctrl = makeController(timers, { autoHide: true, inactivityTimeout: 5000, fadeInMs: 100, fadeOutMs: 100 });
  ctrl.poke();
  ctrl.setInactivityTimeout(1000);
  timers.advance(1000);
  timers.advance(100);
  assert.strictEqual(ctrl.visible, false, 'hides using the updated timeout');
});

test('setFadeDurations updates both directions and emits a change', () => {
  const timers = makeFakeTimers();
  let emissions = 0;
  const ctrl = makeController(timers, {
    autoHide: true, inactivityTimeout: 5000, fadeInMs: 200, fadeOutMs: 300,
    onChange: () => { emissions++; },
  });
  ctrl.setFadeDurations(50, 900);
  assert.strictEqual(ctrl.fadeInMs, 50);
  assert.strictEqual(ctrl.fadeOutMs, 900);
  assert.ok(emissions >= 1);
});
