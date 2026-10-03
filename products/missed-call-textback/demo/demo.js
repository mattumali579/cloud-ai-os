(function () {
  const engine = window.MctbEngine;
  const tenant = engine.exampleTenant();
  const caller = '+14145550123';
  const daytime = '2026-10-03T15:00:00.000Z';
  let state = engine.freshState();
  let started = false;

  const callScreen = document.getElementById('call-screen');
  const callStatus = document.getElementById('call-status');
  const thread = document.getElementById('thread');
  const composer = document.getElementById('composer');
  const draft = document.getElementById('draft');
  const chips = document.getElementById('chips');
  const ownerFeed = document.getElementById('owner-feed');
  const followups = document.getElementById('followups');
  const followupList = document.getElementById('followup-list');
  const stats = document.getElementById('stats');

  function showThread() {
    callScreen.classList.add('hidden');
    thread.classList.remove('hidden');
    composer.classList.remove('hidden');
  }

  function addBubble(who, text) {
    const node = document.createElement('div');
    node.className = 'bubble ' + who;
    node.textContent = text;
    thread.appendChild(node);
    thread.scrollTop = thread.scrollHeight;
  }

  function addOwner(text) {
    if (ownerFeed.querySelector('.empty-note')) ownerFeed.textContent = '';
    const node = document.createElement('div');
    node.className = 'note';
    const label = document.createElement('strong');
    label.textContent = 'Northline';
    node.appendChild(label);
    node.appendChild(document.createTextNode(text));
    ownerFeed.appendChild(node);
  }

  function paintTicket() {
    const conversation = state.conversation || {};
    document.getElementById('t-phone').textContent = started ? caller : '—';
    document.getElementById('t-need').textContent = conversation.need || '—';
    document.getElementById('t-place').textContent = conversation.location || '—';
    document.getElementById('t-urgency').textContent = conversation.urgency || '—';
    const open = state.estimates.filter(function (estimate) { return estimate.status === 'open' || estimate.status === 'won'; });
    const latest = open[open.length - 1] || state.estimates[state.estimates.length - 1];
    document.getElementById('t-estimate').textContent = latest
      ? latest.customer_name + ' · ' + latest.job + ' · ' + engine.formatMoney(latest.amount_cents) + ' · ' + latest.status
      : 'Not logged';
  }

  function paintStats() {
    const missed = state.calls.filter(function (call) { return call.missed; }).length;
    const recovered = state.messages.some(function (message) { return message.direction === 'in' && message.from === caller; }) ? Math.min(missed, 1) : 0;
    const won = state.estimates.filter(function (estimate) { return estimate.status === 'won'; })
      .reduce(function (sum, estimate) { return sum + estimate.amount_cents; }, 0);
    const rows = [
      [String(missed), 'Missed calls'],
      [String(recovered), 'Callers who replied'],
      [engine.formatMoney(won), 'Marked won'],
    ];
    stats.textContent = '';
    rows.forEach(function (row) {
      const card = document.createElement('div');
      const strong = document.createElement('strong');
      strong.textContent = row[0];
      const span = document.createElement('span');
      span.textContent = row[1];
      card.appendChild(strong);
      card.appendChild(span);
      stats.appendChild(card);
    });
  }

  function chip(label, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.addEventListener('click', onClick);
    chips.appendChild(button);
  }

  function paintChips() {
    chips.textContent = '';
    if (!started) return;
    const conversation = state.conversation;
    if (state.suppressed) {
      chip('START', function () { incoming('START'); });
      return;
    }
    if (!conversation || conversation.state === 'awaiting_need') {
      chip('Furnace will not start', function () { incoming('Furnace will not start'); });
    } else if (conversation.state === 'awaiting_location') {
      chip('53211', function () { incoming('53211'); });
    } else if (conversation.state === 'awaiting_urgency') {
      chip('Today', function () { incoming('today please'); });
    } else if (conversation.state === 'handed_off') {
      chip('Log the $4,500 estimate', logEstimate);
      chip('STOP', function () { incoming('STOP'); });
    }
  }

  function apply(decision) {
    (decision.record && decision.record.outbound || []).forEach(function (item) {
      if (item.delivery !== 'twiml' && item.delivery !== 'mock') return;
      if (item.to === caller) addBubble('them', item.body);
    });
    if (decision.record && decision.record.owner_notification) addOwner(decision.record.owner_notification.body);
    state = engine.project(state, decision.record);
    paintTicket();
    paintStats();
    paintChips();
  }

  function incoming(text) {
    addBubble('me', text);
    const decision = engine.handleInboundSms(engine.smsContext(state, tenant, {
      now: daytime,
      from: caller,
      to: tenant.twilio_number,
      body: text,
    }));
    apply(decision);
  }

  function missCall() {
    if (started) return;
    started = true;
    callStatus.textContent = 'Calling…';
    document.getElementById('miss-call').disabled = true;
    window.setTimeout(function () {
      callStatus.textContent = 'No answer';
      window.setTimeout(function () {
        const decision = engine.handleVoice(engine.voiceContext(state, tenant, {
          now: daytime,
          from: caller,
          to: tenant.twilio_number,
          callSid: 'CA-DEMO',
          publicBaseUrl: 'https://text.example',
        }));
        showThread();
        apply(decision);
      }, 700);
    }, 700);
  }

  function logEstimate() {
    const decision = engine.handleAction({
      now: daytime,
      token: 'demo',
      action: 'estimate',
      customer_name: 'Pat Keller',
      phone: caller,
      job: 'furnace replacement',
      amount: '4500',
      tenant: tenant,
      estimates: state.estimates,
      sentToday: state.sentToday,
    }, { TWILIO_MODE: 'mock' });
    state = engine.project(state, decision.record);
    const sequence = engine.previewFollowupSequence(tenant, {
      customer_name: 'Pat Keller',
      phone: caller,
      job: 'furnace replacement',
      amount_cents: 450000,
    }, daytime);
    followupList.textContent = '';
    sequence.forEach(function (step) {
      const item = document.createElement('li');
      const label = document.createElement('span');
      label.textContent = step.label + ' · ' + step.when;
      item.appendChild(label);
      item.appendChild(document.createTextNode(step.body));
      followupList.appendChild(item);
    });
    followups.classList.remove('hidden');
    addOwner('Estimate logged for Pat Keller, furnace replacement, ' + engine.formatMoney(450000) + '.');
    paintTicket();
    paintStats();
    paintChips();
  }

  document.getElementById('miss-call').addEventListener('click', missCall);
  composer.addEventListener('submit', function (event) {
    event.preventDefault();
    const text = draft.value.trim();
    if (!text || !started) return;
    draft.value = '';
    incoming(text);
  });
  paintStats();
})();
