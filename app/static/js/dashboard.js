(function () {
  const TOKEN_KEY = 'governance_jwt';

  function getToken() {
    return document.getElementById('token').value || localStorage.getItem(TOKEN_KEY) || '';
  }

  function setToken(t) {
    localStorage.setItem(TOKEN_KEY, t);
    document.getElementById('token').value = t;
  }

  function headers() {
    const t = getToken();
    const h = { 'Content-Type': 'application/json' };
    if (t) h['Authorization'] = 'Bearer ' + t;
    return h;
  }

  function setApiStatus(ok) {
    const el = document.getElementById('api-status');
    el.className = 'status-badge ' + (ok ? 'status-ok' : 'status-warn');
    el.innerHTML = '<span class="status-dot"></span> ' + (ok ? 'API ready' : 'Set token');
  }

  document.getElementById('save-token').addEventListener('click', function () {
    const t = document.getElementById('token').value.trim();
    if (t) setToken(t);
    setApiStatus(!!getToken());
  });

  if (localStorage.getItem(TOKEN_KEY)) {
    document.getElementById('token').value = localStorage.getItem(TOKEN_KEY);
    setApiStatus(true);
  }

  async function fetchJson(path, options = {}) {
    const res = await fetch(path, { ...options, headers: { ...headers(), ...options.headers } });
    const text = await res.text();
    if (!res.ok) throw new Error(res.status === 401 ? 'Unauthorized: set a valid JWT token.' : (text || res.statusText));
    try {
      return JSON.parse(text);
    } catch (_) {
      return text;
    }
  }

  async function loadHealth() {
    try {
      const data = await fetchJson('/health');
      document.getElementById('health-text').textContent = data.status === 'ok' ? 'OK' : JSON.stringify(data);
      document.getElementById('health-text').classList.remove('placeholder');
    } catch (e) {
      document.getElementById('health-text').textContent = 'Error: ' + e.message;
    }
  }

  async function loadAiStatus() {
    try {
      const res = await fetch('/v1/ai/status/public');
      const data = await res.json();
      const el = document.getElementById('ai-status-text');
      el.textContent = data.ai_available ? 'AI available (LLM configured)' : 'AI heuristics only (no API key)';
      el.classList.remove('placeholder');
    } catch (e) {
      document.getElementById('ai-status-text').textContent = 'Could not load: ' + e.message;
    }
  }

  document.getElementById('load-cr-summary').addEventListener('click', async function () {
    const el = document.getElementById('cr-summary');
    el.textContent = 'Loading…';
    try {
      const data = await fetchJson('/v1/ai/summarize/change-requests?limit=20');
      el.textContent = data.summary || 'No data';
      el.classList.remove('placeholder');
    } catch (e) {
      el.textContent = 'Error: ' + e.message;
    }
  });

  document.getElementById('load-audit-summary').addEventListener('click', async function () {
    const el = document.getElementById('audit-summary');
    el.textContent = 'Loading…';
    try {
      const data = await fetchJson('/v1/ai/summarize/audit?limit=25');
      el.textContent = data.summary || 'No data';
      el.classList.remove('placeholder');
    } catch (e) {
      el.textContent = 'Error: ' + e.message;
    }
  });

  document.getElementById('load-anomalies').addEventListener('click', async function () {
    const el = document.getElementById('anomalies-text');
    el.textContent = 'Loading…';
    try {
      const data = await fetchJson('/v1/ai/anomalies?hours=72');
      let text = data.summary || 'No anomalies.';
      if (data.anomalies && data.anomalies.length) {
        text += '\n\n' + data.anomalies.map(function (a) { return a.message || a.hour; }).join('\n');
      }
      el.textContent = text;
      el.classList.remove('placeholder');
    } catch (e) {
      el.textContent = 'Error: ' + e.message;
    }
  });

  document.getElementById('load-insights').addEventListener('click', async function () {
    const el = document.getElementById('full-insights');
    el.textContent = 'Loading…';
    try {
      const data = await fetchJson('/v1/ai/insights');
      let text = '';
      if (data.change_requests_summary) text += 'Change requests: ' + data.change_requests_summary + '\n\n';
      if (data.audit_summary) text += 'Audit: ' + data.audit_summary + '\n\n';
      if (data.anomalies && data.anomalies.summary) text += 'Anomalies: ' + data.anomalies.summary;
      el.textContent = text || JSON.stringify(data);
      el.classList.remove('placeholder');
    } catch (e) {
      el.textContent = 'Error: ' + e.message;
    }
  });

  document.getElementById('run-nl-query').addEventListener('click', async function () {
    const q = document.getElementById('nl-query').value.trim();
    const resultEl = document.getElementById('nl-result');
    const errEl = document.getElementById('nl-error');
    errEl.textContent = '';
    resultEl.style.display = 'none';
    if (!q) {
      errEl.textContent = 'Enter a query.';
      return;
    }
    try {
      const data = await fetchJson('/v1/ai/nl-query', {
        method: 'POST',
        body: JSON.stringify({ query: q, limit: 50 })
      });
      resultEl.style.display = 'block';
      resultEl.textContent = JSON.stringify(data, null, 2);
    } catch (e) {
      errEl.textContent = e.message;
    }
  });

  document.getElementById('suggest-flag').addEventListener('click', async function () {
    const desc = document.getElementById('flag-desc').value.trim();
    const resultEl = document.getElementById('flag-result');
    const errEl = document.getElementById('flag-error');
    errEl.textContent = '';
    resultEl.style.display = 'none';
    if (!desc) {
      errEl.textContent = 'Enter a description.';
      return;
    }
    try {
      const data = await fetchJson('/v1/ai/suggest/flag-name', {
        method: 'POST',
        body: JSON.stringify({ description: desc })
      });
      resultEl.style.display = 'block';
      resultEl.textContent = 'Suggested key: ' + (data.suggested_key || data) + (data.source ? ' (source: ' + data.source + ')' : '');
    } catch (e) {
      errEl.textContent = e.message;
    }
  });

  loadHealth();
  loadAiStatus();
})();
