(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.GameLog = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  // ── In-game chat/log (issue #32) ───────────────────────────────────────
  // A unified, lightweight message system that every XPilot client (web,
  // pyodide) renders as an in-game chat/log panel. Gameplay and server
  // events both publish into it:
  //
  //   * Server-authored events arrive as {"type": "game_event", "seq": N,
  //     "event": "join"|"leave"|"death"|"chat"|"announce"|..., ...}.
  //     The monotonically increasing `seq` lets the LogBuffer drop
  //     duplicates, fill out-of-order gaps, and keep a consistent order.
  //   * Local gameplay events (single-player deaths, power-up pickups,
  //     offline chat) are pushed without a `seq` and simply appended.
  //
  // Only the most recent `maxMessages` are kept (configurable), messages
  // auto-scroll to the latest, and each event type has its own colour/icon.

  // ── Inline chat auto-hide (issue #37) ─────────────────────────────────
  // An overlay controller that keeps the chat visible while there is
  // activity and fades it out after a configurable period of inactivity.
  // DOM-free so the fade/inactivity state machine can be unit tested; the
  // InlineChatUI below binds it to the page.

  function clampInt(v, lo, hi, dflt) {
    const n = Number(v);
    if (!isFinite(n)) return dflt;
    return Math.max(lo, Math.min(hi, Math.round(n)));
  }

  function sequenceOf(ev) {
    if (!ev || ev.seq == null) return null;
    const seq = Number(ev.seq);
    return Number.isSafeInteger(seq) && seq > 0 ? seq : null;
  }

  function InlineChatController(opts) {
    opts = opts || {};
    this.autoHide = opts.autoHide !== false;
    this.inactivityTimeout = clampInt(opts.inactivityTimeout, 1000, 60000, 3000);
    this.fadeInMs = clampInt(opts.fadeInMs, 0, 3000, 250);
    this.fadeOutMs = clampInt(opts.fadeOutMs, 0, 5000, 700);
    this.onChange = typeof opts.onChange === 'function' ? opts.onChange : null;
    this._setTimeout = opts.setTimeout || function (fn, ms) { return setTimeout(fn, ms); };
    this._clearTimeout = opts.clearTimeout || function (id) { clearTimeout(id); };
    // Start hidden when auto-hide is on (nothing to show yet); stay visible
    // from the start when the user opted to keep the chat permanently shown.
    this.visible = !this.autoHide;
    this.opacity = this.visible ? 1 : 0;
    this._timers = {};
  }

  InlineChatController.prototype._emit = function () {
    if (this.onChange) this.onChange();
  };

  InlineChatController.prototype._clearOne = function (name) {
    if (Object.prototype.hasOwnProperty.call(this._timers, name)) {
      this._clearTimeout(this._timers[name]);
      delete this._timers[name];
    }
  };

  InlineChatController.prototype._clear = function () {
    for (const k in this._timers) this._clearOne(k);
  };

  InlineChatController.prototype._after = function (name, ms, fn) {
    this._clearOne(name);
    if (ms <= 0) { fn(); return; }
    const self = this;
    this._timers[name] = this._setTimeout(function () {
      delete self._timers[name];
      fn();
    }, ms);
  };

  // (Re)arm the inactivity fade-out, if auto-hide is enabled and the overlay
  // is currently shown.
  InlineChatController.prototype._scheduleHide = function () {
    if (!this.autoHide || !this.visible) return;
    const self = this;
    this._after('hide', this.inactivityTimeout, function () {
      self.opacity = 0;
      self._emit();
      // Wait for the fade-out transition, then drop the element.
      self._after('off', self.fadeOutMs, function () {
        self.visible = false;
        self._emit();
      });
    });
  };

  // New activity: show the overlay and reset the inactivity timer.
  InlineChatController.prototype.poke = function () {
    this._clear();
    this.visible = true;
    this.opacity = 1;
    this._emit();
    this._scheduleHide();
  };

  // While the input is focused the chat stays up (no fade-out mid-typing).
  InlineChatController.prototype.focus = function () {
    this._clear();
    this.visible = true;
    this.opacity = 1;
    this._emit();
  };

  // Leaving the input re-arms the inactivity timer.
  InlineChatController.prototype.blur = function () { this.poke(); };

  InlineChatController.prototype.setAutoHide = function (v) {
    v = !!v;
    if (v === this.autoHide) { this._scheduleHide(); return; }
    this.autoHide = v;
    if (v) {
      this.poke(); // begin a fresh inactivity window
    } else {
      // Keep it permanently visible.
      this._clear();
      this.visible = true;
      this.opacity = 1;
      this._emit();
    }
  };

  InlineChatController.prototype.setInactivityTimeout = function (ms) {
    this.inactivityTimeout = clampInt(ms, 1000, 60000, 3000);
    this._scheduleHide();
  };

  InlineChatController.prototype.setFadeDurations = function (inMs, outMs) {
    this.fadeInMs = clampInt(inMs, 0, 3000, 250);
    this.fadeOutMs = outMs == null ? this.fadeInMs : clampInt(outMs, 0, 5000, 700);
    this._emit();
  };

  // ── Event type metadata (colour + icon per event) ──────────────────────
  const EVENT_META = {
    join:     { color: '#7fdc8f', icon: '→' },
    leave:    { color: '#e0a060', icon: '←' },
    death:    { color: '#ff7070', icon: '☠' },
    kill:     { color: '#ffb080', icon: '⚔' },
    pickup:   { color: '#ffcc00', icon: '✦' },
    flag:     { color: '#80c0ff', icon: '⚑' },
    team:     { color: '#c39bd3', icon: '▤' },
    announce: { color: '#e0e0ff', icon: '📢' },
    chat:     { color: '#e8e8e8', icon: '💬' },
  };

  function eventMeta(event) {
    const meta = EVENT_META[event] || {};
    return {
      color: meta.color || '#d8d8e8',
      icon: meta.icon || '•',
    };
  }

  // ── Formatting helpers (pure) ───────────────────────────────────────────
  function pad(n) { return String(n).padStart(2, '0'); }

  // Accepts an epoch-ms number or a Date/string; renders HH:MM:SS (local time).
  function formatTimestamp(ts) {
    const d = ts ? new Date(ts) : new Date();
    if (isNaN(d.getTime())) return '';
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function nameOf(id, name) {
    if (name) return String(name);
    return id ? String(id).slice(0, 6) : '?';
  }

  // Render one raw event into the human-readable line shown in the log.
  function formatEvent(ev, myId) {
    if (!ev || typeof ev !== 'object') return '';
    const kind = ev.event || ev.kind || 'announce';
    const isMe = myId != null && ev.id === myId;
    const who = nameOf(ev.id, ev.name);

    switch (kind) {
      case 'join':
        return `${who} joined the game`;
      case 'leave':
        return `${who} left the game`;
      case 'death': {
        if (isMe && ev.killer && ev.killer !== '') {
          const kName = ev.killerName || nameOf(ev.killer, null);
          return `You were destroyed by ${kName}`;
        }
        if (ev.killer && ev.killer !== '') {
          const kName = ev.killerName || nameOf(ev.killer, null);
          return `${who} was destroyed by ${kName}`;
        }
        return isMe ? 'You were destroyed' : `${who} was destroyed`;
      }
      case 'kill':
        return `${who} eliminated ${ev.targetName || nameOf(ev.target, null)}`;
      case 'pickup':
        return `${who} picked up ${ev.item || ev.kind || 'a power-up'}`;
      case 'flag':
        return `${who} captured the flag`;
      case 'team':
        return `${who} changed team`;
      case 'announce':
        return ev.text || 'Server announcement';
      case 'chat':
        return `${who}: ${ev.text || ''}`;
      default:
        return ev.text || ev.message || kind;
    }
  }

  // ── LogBuffer (pure, DOM-free) ──────────────────────────────────────────
  // Holds an ordered, de-duplicated, bounded list of rendered rows.
  const DEFAULT_MAX_MESSAGES = 60;

  function LogBuffer(opts) {
    opts = opts || {};
    this.max = Math.max(1, Math.floor(Number(opts.maxMessages) || DEFAULT_MAX_MESSAGES));
    this.showTimestamps = opts.showTimestamps !== false;
    this.myId = opts.myId || null;
    this.rows = [];            // [{seq, time, text, color, icon, raw}]
    this._lastSeq = 0;         // highest seq applied to `rows`
    this._pending = new Map(); // seq -> event, for out-of-order arrivals
  }

  LogBuffer.prototype.setMaxMessages = function (n) {
    this.max = Math.max(1, Math.floor(Number(n) || DEFAULT_MAX_MESSAGES));
    while (this.rows.length > this.max) this.rows.shift();
    return this.rows.length;
  };

  LogBuffer.prototype.setShowTimestamps = function (v) { this.showTimestamps = !!v; };
  LogBuffer.prototype.setMyId = function (id) { this.myId = id; };
  LogBuffer.prototype.count = function () { return this.rows.length; };

  // Push a single raw event. Returns the array of newly appended rows
  // (usually one), or null when the event was stale/duplicate, or an empty
  // array when the event was buffered waiting for an out-of-order gap.
  LogBuffer.prototype.push = function (ev) {
    if (!ev || typeof ev !== 'object') return null;
    const seq = sequenceOf(ev);

    if (seq == null) {
      // Local/unsynchronised event: append in arrival order.
      return this._append(ev);
    }
    if (seq <= this._lastSeq) return null;          // duplicate / stale
    if (seq > this._lastSeq + 1) {
      // Out of order: hold it until the missing gap arrives.
      if (!this._pending.has(seq)) {
        if (this._pending.size >= 500) {
          // Bound the pending map so a gap never leaks memory forever.
          const oldest = [...this._pending.keys()].sort((a, b) => a - b)[0];
          this._pending.delete(oldest);
        }
        this._pending.set(seq, ev);
      }
      return [];
    }
    // Contiguous: apply it, then drain any pending events that are now in order.
    this._lastSeq = seq;
    const out = this._append(ev) || [];
    let next = this._lastSeq + 1;
    while (this._pending.has(next)) {
      const e = this._pending.get(next);
      this._pending.delete(next);
      this._lastSeq = next;
      const added = this._append(e);
      if (added) for (const r of added) out.push(r);
      next = this._lastSeq + 1;
    }
    return out;
  };

  LogBuffer.prototype._append = function (ev) {
    const text = formatEvent(ev, this.myId);
    if (!text) return null;
    const meta = eventMeta(ev.event || ev.kind);
    const row = {
      seq: sequenceOf(ev),
      time: ev.time != null ? ev.time : Date.now(),
      text,
      color: meta.color,
      icon: meta.icon,
      raw: ev,
    };
    this.rows.push(row);
    while (this.rows.length > this.max) this.rows.shift();
    return [row];
  };

  // Apply a batch of history events (e.g. what the server sends a late
  // joiner). Sorted by seq so it applies contiguously.
  LogBuffer.prototype.loadHistory = function (events) {
    const added = [];
    if (!Array.isArray(events)) return added;
    const sorted = events
      .filter((e) => sequenceOf(e) != null)
      .sort((a, b) => sequenceOf(a) - sequenceOf(b));
    for (const e of sorted) {
      const seq = sequenceOf(e);
      if (seq <= this._lastSeq) continue;
      // A bounded history may begin after the first server event. Treat the
      // first retained event as the new baseline instead of waiting forever
      // for events that are no longer available.
      if (this._lastSeq === 0 && !this.rows.length && !this._pending.size) {
        this._lastSeq = seq - 1;
      }
      this._lastSeq = seq;
      const appended = this._append(e);
      if (appended) for (const r of appended) added.push(r);
    }
    return added;
  };

  LogBuffer.prototype.clear = function () {
    this.rows.length = 0;
    this._pending.clear();
    this._lastSeq = 0;
    return 0;
  };

  // ── GameLogUI (thin DOM binding on top of a LogBuffer) ──────────────────
  function GameLogUI(opts) {
    opts = opts || {};
    this.buffer = new LogBuffer({
      maxMessages: opts.maxMessages,
      showTimestamps: opts.showTimestamps,
      myId: opts.myId,
    });
    this.listEl = opts.listEl || null;
    this.listEls = this.listEl ? (Array.isArray(this.listEl) ? this.listEl : [this.listEl]) : [];
    this.countEl = opts.countEl || null;
  }

  GameLogUI.prototype.push = function (ev) {
    const rows = this.buffer.push(ev);
    if (rows) this._render();
    return rows;
  };

  GameLogUI.prototype.loadHistory = function (events) {
    const rows = this.buffer.loadHistory(events);
    this._render();
    return rows;
  };

  GameLogUI.prototype.setMaxMessages = function (n) {
    this.buffer.setMaxMessages(n);
    this._render();
  };

  GameLogUI.prototype.setShowTimestamps = function (v) {
    this.buffer.setShowTimestamps(v);
    this._render();
  };

  GameLogUI.prototype.setMyId = function (id) {
    this.buffer.setMyId(id);
  };

  GameLogUI.prototype.clear = function () {
    this.buffer.clear();
    this._render();
  };

  GameLogUI.prototype.count = function () {
    return this.buffer.count();
  };

  // Rebuild the visible rows. Kept lightweight: events are relatively rare
  // and the row count is bounded, so a full re-render on change is cheap and
  // always stays in sync with the buffer.
  GameLogUI.prototype._render = function () {
    if (!this.listEls.length) return;
    const frag = document.createDocumentFragment();
    for (const row of this.buffer.rows) {
      const el = document.createElement('div');
      el.className = 'gl-row';
      el.style.borderLeftColor = row.color;
      if (this.buffer.showTimestamps) {
        const ts = document.createElement('span');
        ts.className = 'gl-time';
        ts.textContent = formatTimestamp(row.time);
        el.appendChild(ts);
      }
      const icon = document.createElement('span');
      icon.className = 'gl-icon';
      icon.textContent = row.icon;
      icon.style.color = row.color;
      const text = document.createElement('span');
      text.className = 'gl-text';
      text.textContent = row.text;
      el.appendChild(icon);
      el.appendChild(text);
      frag.appendChild(el);
    }
    for (const el of this.listEls) {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
      el.innerHTML = '';
      el.appendChild(frag.cloneNode(true));
      if (atBottom) el.scrollTop = el.scrollHeight;
    }
    if (this.countEl) this.countEl.textContent = String(this.buffer.rows.length);
  };

  // ── InlineChatUI (auto-hiding inline overlay, issue #37) ───────────────
  // Renders the LogBuffer as an inline overlay inside the game view (no box,
  // no header) and fades it out after a configurable period of inactivity.
  // It reappears instantly whenever a new message or event is pushed.
  function InlineChatUI(opts) {
    GameLogUI.call(this, opts);
    opts = opts || {};
    this.rootEl = opts.rootEl || null;
    this.inputEl = opts.inputEl || null;
    this.controller = new InlineChatController({
      autoHide: opts.autoHide,
      inactivityTimeout: opts.inactivityTimeout,
      fadeInMs: opts.fadeInMs,
      fadeOutMs: opts.fadeOutMs,
      setTimeout: opts.setTimeout,
      clearTimeout: opts.clearTimeout,
      onChange: this._sync.bind(this),
    });
    this._lastOpacity = -1;
    if (this.inputEl) {
      this.inputEl.addEventListener('focus', () => this.controller.focus());
      this.inputEl.addEventListener('blur', () => this.controller.blur());
    }
    this._sync();
  }
  InlineChatUI.prototype = Object.create(GameLogUI.prototype);
  InlineChatUI.prototype.constructor = InlineChatUI;

  InlineChatUI.prototype.push = function (ev) {
    const rows = GameLogUI.prototype.push.call(this, ev);
    if (rows && rows.length) this.controller.poke();
    return rows;
  };

  InlineChatUI.prototype.loadHistory = function (events) {
    const rows = GameLogUI.prototype.loadHistory.call(this, events);
    if (rows && rows.length) this.controller.poke();
    return rows;
  };

  InlineChatUI.prototype.setAutoHide = function (v) { this.controller.setAutoHide(!!v); };
  InlineChatUI.prototype.setAlwaysVisible = function (v) { this.controller.setAutoHide(!v); };
  InlineChatUI.prototype.setInactivityTimeout = function (ms) { this.controller.setInactivityTimeout(ms); };
  InlineChatUI.prototype.setFadeDurations = function (inMs, outMs) { this.controller.setFadeDurations(inMs, outMs); };
  InlineChatUI.prototype.poke = function () { this.controller.poke(); };
  InlineChatUI.prototype.hide = function () {
    this.controller._clear();
    this.controller.visible = false;
    this.controller.opacity = 0;
    this.controller._emit();
  };

  // Apply the controller state to the DOM: opacity transitions use the
  // configured fade duration in the direction we are moving.
  InlineChatUI.prototype._sync = function () {
    const c = this.controller;
    const el = this.rootEl;
    if (el) {
      const dur = c.opacity >= this._lastOpacity ? c.fadeInMs : c.fadeOutMs;
      el.style.transition = 'opacity ' + dur + 'ms ease';
      el.style.opacity = String(c.opacity);
      el.style.display = c.visible ? '' : 'none';
      this._lastOpacity = c.opacity;
    }
  };

  function createUI(opts) {
    return new GameLogUI(opts);
  }

  function createInlineUI(opts) {
    return new InlineChatUI(opts);
  }

  return {
    EVENT_META,
    eventMeta,
    formatTimestamp,
    formatEvent,
    LogBuffer,
    GameLogUI,
    InlineChatController,
    InlineChatUI,
    createUI,
    createInlineUI,
    DEFAULT_MAX_MESSAGES,
  };
});
