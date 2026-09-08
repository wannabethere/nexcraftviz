/**
 * nexcraftviz embed — a charting conversation you can drop into any tool.
 *
 *   <script type="module" src=".../embed/nexcraftviz.js"></script>
 *   <nexcraftviz-chat endpoint="https://charts.example.com"></nexcraftviz-chat>
 *
 * Two modes, matching the Python side:
 *
 *   HOSTED — the server owns the model. Set `endpoint` and nothing else; the
 *   component POSTs each message to /turn.
 *
 *   DRIVEN — the host owns the model. Set `mode="driven"` and assign
 *   `el.onPropose`. The component asks the server what to do (a prompt and a
 *   schema, no model call), hands it to you, and commits whatever you return:
 *
 *     el.onPropose = async ({ prompt, skill }) => myModel(prompt);
 *
 * Styling comes from CSS custom properties, which inherit through the shadow
 * root — so the widget picks up the host page's `--nxv-*` tokens if it defines
 * any, and falls back to the served theme if not. That is why this is a custom
 * element and not an iframe: an iframe cannot inherit a theme, and its charts
 * cannot share a layout with the page around them.
 */

const VEGA_CDN = {
  vega: 'https://cdn.jsdelivr.net/npm/vega@5',
  vegaLite: 'https://cdn.jsdelivr.net/npm/vega-lite@5',
  vegaEmbed: 'https://cdn.jsdelivr.net/npm/vega-embed@6',
};

/** Load vega-embed once, only if the host page has not already. */
let vegaReady = null;
function ensureVega() {
  if (window.vegaEmbed) return Promise.resolve(window.vegaEmbed);
  if (vegaReady) return vegaReady;
  vegaReady = ['vega', 'vegaLite', 'vegaEmbed']
    .reduce(
      (chain, key) =>
        chain.then(
          () =>
            new Promise((resolve, reject) => {
              const script = document.createElement('script');
              script.src = VEGA_CDN[key];
              script.onload = resolve;
              script.onerror = () => reject(new Error(`failed to load ${key}`));
              document.head.appendChild(script);
            })
        ),
      Promise.resolve()
    )
    .then(() => window.vegaEmbed);
  return vegaReady;
}

/** Thin client for the HTTP surface. Usable on its own as a JS SDK. */
export class NexcraftvizClient {
  constructor(endpoint, { fetchImpl = null, headers = {} } = {}) {
    this.endpoint = String(endpoint || '').replace(/\/$/, '');
    // `fetch` must stay bound to the global. Stored unbound and then called as
    // `this.fetch(...)`, the browser throws "Illegal invocation" — a confusing
    // failure a long way from its cause.
    this.fetch = (fetchImpl || globalThis.fetch).bind(globalThis);
    this.headers = { 'content-type': 'application/json', ...headers };
  }

  async _call(path, { method = 'POST', body } = {}) {
    const response = await this.fetch(`${this.endpoint}${path}`, {
      method,
      headers: this.headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      // The server sends an actionable message; surfacing the status alone
      // would throw that away.
      let detail = `${response.status} ${response.statusText}`;
      try {
        const payload = await response.json();
        if (payload && payload.detail) detail = payload.detail;
      } catch (_) { /* keep the status line */ }
      throw new Error(detail);
    }
    return response.json();
  }

  createSession(body = {}) { return this._call('/v1/sessions', { body }); }
  getSession(id) { return this._call(`/v1/sessions/${id}`, { method: 'GET' }); }
  turn(id, message, skill = '') { return this._call(`/v1/sessions/${id}/turn`, { body: { message, skill } }); }
  propose(id, message, skill = '') { return this._call(`/v1/sessions/${id}/propose`, { body: { message, skill } }); }
  commit(id, payload) { return this._call(`/v1/sessions/${id}/commit`, { body: payload }); }
  undo(id) { return this._call(`/v1/sessions/${id}/undo`); }
  redo(id) { return this._call(`/v1/sessions/${id}/redo`); }
  tools(style = 'openai') { return this._call(`/v1/tools?style=${style}`, { method: 'GET' }); }
}

const STYLES = `
:host {
  --nxv-embed-height: 520px;
  display: block;
  font-family: var(--nxv-font, system-ui, -apple-system, sans-serif);
  font-size: var(--nxv-size-base, 13px);
  color: var(--nxv-text, #0f172a);
}
.wrap {
  background: var(--nxv-surface, #fff);
  border: 1px solid var(--nxv-border, #e2e8f0);
  border-radius: var(--nxv-radius-md, 6px);
  display: grid;
  grid-template-rows: 1fr auto;
  height: var(--nxv-embed-height);
  overflow: hidden;
}
.doc { overflow: auto; padding: 16px; }
.doc:empty::after {
  color: var(--nxv-text-muted, #94a3b8);
  content: attr(data-placeholder);
  display: block;
  padding: 32px 8px;
  text-align: center;
}
.chart { display: block; width: 100%; }
.log { border-top: 1px solid var(--nxv-border, #e2e8f0); max-height: 156px; overflow: auto; padding: 8px 16px; }
.msg { display: flex; gap: 8px; padding: 4px 0; }
.msg__who { color: var(--nxv-text-muted, #94a3b8); flex: 0 0 auto; font-size: 11px; padding-top: 2px; width: 56px; }
.msg__text { min-width: 0; }
.msg--error .msg__text { color: var(--nxv-negative, #b91c1c); }
.msg__changes { color: var(--nxv-text-secondary, #475569); font-size: 11px; margin-top: 2px; }
.bar { border-top: 1px solid var(--nxv-border, #e2e8f0); display: flex; gap: 8px; padding: 10px 12px; }
input {
  background: var(--nxv-background, #fff);
  border: 1px solid var(--nxv-border-strong, #cbd5e1);
  border-radius: var(--nxv-radius-sm, 3px);
  color: inherit; flex: 1; font: inherit; padding: 7px 10px;
}
input:focus { border-color: var(--nxv-accent, #0c8ba6); outline: 2px solid transparent; }
button {
  background: var(--nxv-accent, #0c8ba6); border: 0;
  border-radius: var(--nxv-radius-sm, 3px); color: var(--nxv-text-inverse, #fff);
  cursor: pointer; font: inherit; padding: 7px 14px;
}
button[disabled] { cursor: default; opacity: 0.5; }
button.ghost { background: transparent; border: 1px solid var(--nxv-border-strong, #cbd5e1); color: inherit; }
.busy { color: var(--nxv-text-muted, #94a3b8); font-size: 11px; padding: 4px 16px; }
`;

class NexcraftvizChat extends HTMLElement {
  static get observedAttributes() { return ['endpoint', 'session-id', 'mode', 'placeholder']; }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    /** Assign in driven mode: ({prompt, skill}) => modelOutput */
    this.onPropose = null;
    this._state = null;
    this._busy = false;
  }

  get endpoint() { return this.getAttribute('endpoint') || ''; }
  get mode() { return this.getAttribute('mode') || 'hosted'; }
  get client() {
    if (!this._client) this._client = new NexcraftvizClient(this.endpoint);
    return this._client;
  }
  get state() { return this._state; }

  connectedCallback() {
    this._render();
    const existing = this.getAttribute('session-id');
    if (existing) this._load(existing);
    else if (this.endpoint) this.start();
  }

  /** Begin a session. Pass rows and an optional starting document. */
  async start(body = {}) {
    try {
      this._apply(await this.client.createSession(body));
    } catch (error) {
      this._log('error', error.message);
    }
  }

  async _load(id) {
    try {
      this._apply(await this.client.getSession(id));
    } catch (error) {
      this._log('error', error.message);
    }
  }

  /** Send a message. Works in both modes; the difference is one branch. */
  async send(message, skill = '') {
    const text = String(message || '').trim();
    if (!text || this._busy || !this._state) return;

    this._setBusy(true);
    this._log('you', text);
    try {
      const id = this._state.session_id;
      let payload;

      if (this.mode === 'driven') {
        const proposal = await this.client.propose(id, text, skill);
        let output = null;
        if (proposal.needs_model) {
          if (typeof this.onPropose !== 'function') {
            throw new Error('mode="driven" needs an onPropose handler');
          }
          output = await this.onPropose(proposal);
        }
        payload = await this.client.commit(id, {
          turn_id: proposal.turn_id,
          skill: proposal.skill,
          inputs: proposal.inputs,
          output,
        });
      } else {
        payload = await this.client.turn(id, text, skill);
      }

      this._apply(payload.state);
      this._logTurn(payload.turn);
      this.dispatchEvent(new CustomEvent('nxv-turn', {
        detail: payload, bubbles: true, composed: true,
      }));
    } catch (error) {
      this._log('error', error.message);
    } finally {
      this._setBusy(false);
    }
  }

  async undo() { await this._history('undo'); }
  async redo() { await this._history('redo'); }

  async _history(which) {
    if (!this._state) return;
    try {
      const payload = await this.client[which](this._state.session_id);
      this._apply(payload.state);
      this._log('nxv', payload.undone || payload.redone ? `${which} applied` : `nothing to ${which}`);
    } catch (error) {
      this._log('error', error.message);
    }
  }

  // -- rendering --------------------------------------------------------

  _render() {
    const placeholder = this.getAttribute('placeholder')
      || 'Ask for a chart, or describe a change.';
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="wrap">
        <div class="doc" part="document" data-placeholder="No chart yet."></div>
        <div>
          <div class="log" part="log"></div>
          <div class="busy" hidden>Working…</div>
          <form class="bar">
            <input type="text" placeholder="${placeholder}" aria-label="Message" />
            <button type="submit">Send</button>
            <button type="button" class="ghost" data-action="undo" title="Undo">↩</button>
          </form>
        </div>
      </div>`;

    const form = this.shadowRoot.querySelector('form');
    const input = this.shadowRoot.querySelector('input');
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = input.value;
      input.value = '';
      this.send(text);
    });
    this.shadowRoot.querySelector('[data-action="undo"]')
      .addEventListener('click', () => this.undo());
  }

  _apply(state) {
    this._state = state;
    if (state && state.session_id) this.setAttribute('session-id', state.session_id);
    this._paint();
    this.dispatchEvent(new CustomEvent('nxv-state', {
      detail: state, bubbles: true, composed: true,
    }));
  }

  _paint() {
    const host = this.shadowRoot.querySelector('.doc');
    const state = this._state;
    if (!state || !state.document) { host.innerHTML = ''; return; }

    if (state.kind === 'widget') {
      // The widget's markup is rendered server-side, so the tile vocabulary
      // has one implementation rather than one per client. Its chart specs
      // arrive alongside as `state.specs`: the markup also carries them in an
      // inline <script>, but scripts inserted via innerHTML never execute.
      host.innerHTML = state.html || '';
      const specs = state.specs || {};
      ensureVega().then((embed) => {
        host.querySelectorAll('.nxv-chart[id]').forEach((node) => {
          const spec = specs[node.id];
          if (spec) embed(node, spec, { actions: false }).catch(() => {});
        });
      });
      return;
    }

    host.innerHTML = '<div class="chart"></div>';
    const mount = host.querySelector('.chart');
    ensureVega()
      .then((embed) => embed(mount, state.document, { actions: false }))
      .catch((error) => { mount.textContent = `Chart failed to render: ${error.message}`; });
  }

  _setBusy(busy) {
    this._busy = busy;
    this.shadowRoot.querySelector('.busy').hidden = !busy;
    this.shadowRoot.querySelector('button[type="submit"]').disabled = busy;
  }

  _log(who, text, changes = []) {
    const log = this.shadowRoot.querySelector('.log');
    const row = document.createElement('div');
    row.className = `msg${who === 'error' ? ' msg--error' : ''}`;
    const label = document.createElement('div');
    label.className = 'msg__who';
    label.textContent = who;
    const body = document.createElement('div');
    body.className = 'msg__text';
    body.textContent = text;
    if (changes.length) {
      const detail = document.createElement('div');
      detail.className = 'msg__changes';
      detail.textContent = changes.join(' · ');
      body.appendChild(detail);
    }
    row.append(label, body);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
  }

  _logTurn(turn) {
    if (!turn) return;
    const failures = (turn.failed || []).map((f) => `${f.op}: ${f.reason}`);
    this._log(turn.ok ? 'nxv' : 'error', turn.reply || '(no change)',
      [...(turn.changes || []), ...failures, ...(turn.warnings || [])]);
  }
}

if (!customElements.get('nexcraftviz-chat')) {
  customElements.define('nexcraftviz-chat', NexcraftvizChat);
}

export { NexcraftvizChat };
export default NexcraftvizChat;
