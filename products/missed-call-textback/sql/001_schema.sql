-- BrightReach Missed-Call Text-Back
-- Schema mctb is isolated from Cloud AI OS / outreach tables.
-- Apply to the client's Postgres (the mini PC compose database, or a
-- dedicated database on the existing Cloud AI OS Postgres).

CREATE SCHEMA IF NOT EXISTS mctb;

CREATE TABLE IF NOT EXISTS mctb.tenants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text UNIQUE NOT NULL,
  business_name text NOT NULL,
  twilio_number text UNIQUE NOT NULL,
  owner_name text NOT NULL,
  owner_phone text NOT NULL,
  owner_email text NOT NULL DEFAULT '',
  timezone text NOT NULL DEFAULT 'America/Chicago',
  hours_text text NOT NULL DEFAULT 'Mon-Fri 8am-5pm',
  booking_link text NOT NULL DEFAULT '',
  call_mode text NOT NULL DEFAULT 'forward' CHECK (call_mode IN ('forward', 'dial')),
  ring_timeout_seconds integer NOT NULL DEFAULT 20 CHECK (ring_timeout_seconds BETWEEN 5 AND 40),
  missed_call_template text NOT NULL DEFAULT 'Hi, this is {business_name}. Sorry we missed your call. Reply with what you need and we will get you scheduled. Hours: {hours}. Book: {booking_link}. Reply STOP to opt out.',
  qualify_location_prompt text NOT NULL DEFAULT 'Thanks. What is the service address or ZIP code?',
  qualify_urgency_prompt text NOT NULL DEFAULT 'How urgent is this? Reply TODAY, THIS WEEK, or FLEXIBLE.',
  qualify_done_template text NOT NULL DEFAULT 'Got it. {owner_name} at {business_name} has your request and will follow up shortly. Book now: {booking_link}',
  followup_templates text[] NOT NULL DEFAULT ARRAY[
    'Hi {customer_name}, this is {business_name}. Following up on your {job} estimate for {amount}. Reply with any questions or book here: {booking_link}. Reply STOP to opt out.',
    'Hi {customer_name}, {business_name} can still hold the {amount} price for {job}. Want us to get you on the schedule this week? Reply STOP to opt out.',
    'Last note from {business_name} about the {job} estimate ({amount}). Reply YES to lock it in, or STOP to opt out.'
  ],
  quiet_start text NOT NULL DEFAULT '21:00',
  quiet_end text NOT NULL DEFAULT '08:00',
  missed_call_respects_quiet_hours boolean NOT NULL DEFAULT false,
  daily_sms_limit integer NOT NULL DEFAULT 200 CHECK (daily_sms_limit > 0),
  per_number_daily_limit integer NOT NULL DEFAULT 12 CHECK (per_number_daily_limit > 0),
  dashboard_token text UNIQUE NOT NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mctb.calls (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  call_sid text NOT NULL DEFAULT '',
  from_number text NOT NULL DEFAULT '',
  to_number text NOT NULL DEFAULT '',
  dial_status text NOT NULL DEFAULT '',
  missed boolean NOT NULL DEFAULT false,
  text_sent boolean NOT NULL DEFAULT false,
  suppressed_reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS calls_sid_unique
  ON mctb.calls (tenant_id, call_sid)
  WHERE call_sid <> '';

CREATE INDEX IF NOT EXISTS calls_tenant_created
  ON mctb.calls (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mctb.conversations (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  phone text NOT NULL,
  state text NOT NULL,
  need text,
  location text,
  urgency text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, phone)
);

ALTER TABLE mctb.conversations ADD COLUMN IF NOT EXISTS qualified_at timestamptz;

UPDATE mctb.conversations
SET qualified_at = COALESCE(qualified_at, updated_at)
WHERE qualified_at IS NULL
  AND (
    state = 'handed_off'
    OR (COALESCE(need, '') <> '' AND COALESCE(location, '') <> '' AND COALESCE(urgency, '') <> '')
  );

CREATE TABLE IF NOT EXISTS mctb.messages (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  conversation_id bigint REFERENCES mctb.conversations(id),
  direction text NOT NULL CHECK (direction IN ('in', 'out')),
  from_number text NOT NULL DEFAULT '',
  to_number text NOT NULL DEFAULT '',
  body text NOT NULL,
  twilio_sid text,
  purpose text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_tenant_phone
  ON mctb.messages (tenant_id, from_number, created_at);

CREATE TABLE IF NOT EXISTS mctb.suppressions (
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  phone text NOT NULL,
  reason text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, phone)
);

CREATE TABLE IF NOT EXISTS mctb.estimates (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  customer_name text NOT NULL,
  phone text NOT NULL,
  job text NOT NULL,
  amount_cents integer NOT NULL CHECK (amount_cents >= 0),
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'won', 'lost', 'replied', 'opted_out', 'completed')),
  step_index integer NOT NULL DEFAULT 0,
  next_send_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS estimates_due
  ON mctb.estimates (status, next_send_at);

CREATE TABLE IF NOT EXISTS mctb.outbound_log (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  to_number text NOT NULL,
  from_number text NOT NULL,
  body text NOT NULL,
  purpose text NOT NULL DEFAULT '',
  provider text NOT NULL,
  provider_sid text,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outbound_log_recent
  ON mctb.outbound_log (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mctb.outbound_queue (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  to_number text NOT NULL,
  from_number text NOT NULL,
  body text NOT NULL,
  purpose text NOT NULL DEFAULT '',
  send_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS outbound_queue_due
  ON mctb.outbound_queue (status, send_at);

CREATE TABLE IF NOT EXISTS mctb.owner_notifications (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  channel text NOT NULL,
  body text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION mctb.tenant_public(t mctb.tenants)
RETURNS jsonb
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT jsonb_build_object(
    'id', t.id,
    'slug', t.slug,
    'business_name', t.business_name,
    'twilio_number', t.twilio_number,
    'owner_name', t.owner_name,
    'owner_phone', t.owner_phone,
    'owner_email', t.owner_email,
    'timezone', t.timezone,
    'hours_text', t.hours_text,
    'booking_link', t.booking_link,
    'call_mode', t.call_mode,
    'ring_timeout_seconds', t.ring_timeout_seconds,
    'missed_call_template', t.missed_call_template,
    'qualify_location_prompt', t.qualify_location_prompt,
    'qualify_urgency_prompt', t.qualify_urgency_prompt,
    'qualify_done_template', t.qualify_done_template,
    'followup_templates', to_jsonb(t.followup_templates),
    'quiet_start', t.quiet_start,
    'quiet_end', t.quiet_end,
    'missed_call_respects_quiet_hours', t.missed_call_respects_quiet_hours,
    'daily_sms_limit', t.daily_sms_limit,
    'per_number_daily_limit', t.per_number_daily_limit,
    'active', t.active
  );
$$;

CREATE OR REPLACE FUNCTION mctb.sent_count(p_tenant uuid, p_to text)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
  SELECT count(*)::integer
  FROM mctb.outbound_log o
  WHERE o.tenant_id = p_tenant
    AND o.status IN ('twiml', 'sent', 'mock_sent')
    AND o.created_at > now() - interval '24 hours'
    AND (p_to IS NULL OR o.to_number = p_to);
$$;

CREATE OR REPLACE FUNCTION mctb.load_voice_context(p_to text, p_from text, p_call_sid text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE twilio_number = p_to AND active LIMIT 1;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('tenant', NULL);
  END IF;
  RETURN jsonb_build_object(
    'tenant', mctb.tenant_public(t),
    'duplicate', EXISTS (
      SELECT 1 FROM mctb.calls c
      WHERE c.tenant_id = t.id
        AND c.call_sid = COALESCE(p_call_sid, '')
        AND COALESCE(p_call_sid, '') <> ''
        AND (c.text_sent OR c.missed OR c.suppressed_reason IS NOT NULL)
    ),
    'suppressed', EXISTS (
      SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = t.id AND s.phone = p_from
    ),
    'conversation', (
      SELECT jsonb_build_object('state', c.state, 'need', c.need, 'location', c.location, 'urgency', c.urgency)
      FROM mctb.conversations c
      WHERE c.tenant_id = t.id AND c.phone = p_from
    ),
    'sent_today', mctb.sent_count(t.id, NULL),
    'sent_to_today', mctb.sent_count(t.id, p_from),
    'sent_to_owner_today', mctb.sent_count(t.id, t.owner_phone)
  );
END;
$$;

CREATE OR REPLACE FUNCTION mctb.load_sms_context(p_to text, p_from text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE twilio_number = p_to AND active LIMIT 1;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('tenant', NULL);
  END IF;
  RETURN jsonb_build_object(
    'tenant', mctb.tenant_public(t),
    'suppressed', EXISTS (
      SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = t.id AND s.phone = p_from
    ),
    'suppressed_phones', COALESCE((
      SELECT jsonb_agg(s.phone) FROM mctb.suppressions s WHERE s.tenant_id = t.id
    ), '[]'::jsonb),
    'conversation', (
      SELECT jsonb_build_object('state', c.state, 'need', c.need, 'location', c.location, 'urgency', c.urgency)
      FROM mctb.conversations c
      WHERE c.tenant_id = t.id AND c.phone = p_from
    ),
    'open_estimate', (
      SELECT jsonb_build_object(
        'id', e.id, 'status', e.status, 'phone', e.phone, 'job', e.job,
        'amount_cents', e.amount_cents, 'customer_name', e.customer_name
      )
      FROM mctb.estimates e
      WHERE e.tenant_id = t.id AND e.phone = p_from AND e.status = 'open'
      ORDER BY e.id DESC
      LIMIT 1
    ),
    'estimates', COALESCE((
      SELECT jsonb_agg(x.obj)
      FROM (
        SELECT jsonb_build_object(
          'id', e.id, 'status', e.status, 'phone', e.phone,
          'customer_name', e.customer_name, 'job', e.job
        ) AS obj
        FROM mctb.estimates e
        WHERE e.tenant_id = t.id
        ORDER BY e.id DESC
        LIMIT 50
      ) x
    ), '[]'::jsonb),
    'sent_today', mctb.sent_count(t.id, NULL),
    'sent_to_today', mctb.sent_count(t.id, p_from),
    'sent_to_owner_today', mctb.sent_count(t.id, t.owner_phone)
  );
END;
$$;

CREATE OR REPLACE FUNCTION mctb.action_context(p_token text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE dashboard_token = p_token AND active LIMIT 1;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('tenant', NULL);
  END IF;
  RETURN jsonb_build_object(
    'tenant', mctb.tenant_public(t),
    'suppressed_phones', COALESCE((
      SELECT jsonb_agg(s.phone) FROM mctb.suppressions s WHERE s.tenant_id = t.id
    ), '[]'::jsonb),
    'estimates', COALESCE((
      SELECT jsonb_agg(x.obj)
      FROM (
        SELECT jsonb_build_object(
          'id', e.id, 'status', e.status, 'phone', e.phone,
          'customer_name', e.customer_name, 'job', e.job
        ) AS obj
        FROM mctb.estimates e
        WHERE e.tenant_id = t.id
        ORDER BY e.id DESC
        LIMIT 50
      ) x
    ), '[]'::jsonb),
    'sent_today', mctb.sent_count(t.id, NULL),
    'sent_to_today', 0,
    'sent_to_owner_today', mctb.sent_count(t.id, t.owner_phone)
  );
END;
$$;

CREATE OR REPLACE FUNCTION mctb.store_outbound(
  p_tenant uuid,
  p_items jsonb,
  p_conversation bigint
) RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  item jsonb;
  n integer := 0;
BEGIN
  FOR item IN SELECT value FROM jsonb_array_elements(COALESCE(p_items, '[]'::jsonb))
  LOOP
    IF EXISTS (
      SELECT 1 FROM mctb.suppressions s
      WHERE s.tenant_id = p_tenant AND s.phone = COALESCE(item->>'to', '')
    ) AND COALESCE(item->>'purpose', '') NOT IN ('stop_confirm', 'help', 'start_confirm') THEN
      CONTINUE;
    END IF;

    IF item->>'delivery' = 'queue' THEN
      INSERT INTO mctb.outbound_queue (tenant_id, to_number, from_number, body, purpose, send_at, status)
      VALUES (
        p_tenant,
        item->>'to',
        item->>'from',
        COALESCE(item->>'body', ''),
        COALESCE(item->>'purpose', ''),
        COALESCE(NULLIF(item->>'send_at', '')::timestamptz, now()),
        'pending'
      );
    ELSIF item->>'delivery' IN ('twiml', 'mock') THEN
      INSERT INTO mctb.outbound_log (tenant_id, to_number, from_number, body, purpose, provider, status)
      VALUES (
        p_tenant,
        item->>'to',
        item->>'from',
        COALESCE(item->>'body', ''),
        COALESCE(item->>'purpose', ''),
        item->>'delivery',
        CASE WHEN item->>'delivery' = 'twiml' THEN 'twiml' ELSE 'mock_sent' END
      );
      INSERT INTO mctb.messages (tenant_id, conversation_id, direction, from_number, to_number, body, purpose)
      VALUES (
        p_tenant,
        p_conversation,
        'out',
        item->>'from',
        item->>'to',
        COALESCE(item->>'body', ''),
        COALESCE(item->>'purpose', '')
      );
    END IF;
    n := n + 1;
  END LOOP;
  RETURN n;
END;
$$;

CREATE OR REPLACE FUNCTION mctb.upsert_conversation(
  p_tenant uuid,
  p_phone text,
  p_conversation jsonb
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
  v_id bigint;
BEGIN
  IF p_conversation IS NULL OR p_conversation = 'null'::jsonb OR COALESCE(p_phone, '') = '' THEN
    RETURN NULL;
  END IF;
  INSERT INTO mctb.conversations (tenant_id, phone, state, need, location, urgency, qualified_at)
  VALUES (
    p_tenant,
    p_phone,
    COALESCE(p_conversation->>'state', 'awaiting_need'),
    NULLIF(p_conversation->>'need', ''),
    NULLIF(p_conversation->>'location', ''),
    NULLIF(p_conversation->>'urgency', ''),
    CASE WHEN COALESCE(p_conversation->>'state', '') = 'handed_off' THEN now() ELSE NULL END
  )
  ON CONFLICT (tenant_id, phone) DO UPDATE SET
    state = EXCLUDED.state,
    need = COALESCE(EXCLUDED.need, mctb.conversations.need),
    location = COALESCE(EXCLUDED.location, mctb.conversations.location),
    urgency = COALESCE(EXCLUDED.urgency, mctb.conversations.urgency),
    qualified_at = COALESCE(
      mctb.conversations.qualified_at,
      CASE WHEN EXCLUDED.state = 'handed_off' THEN now() ELSE NULL END
    ),
    updated_at = now()
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION mctb.apply_voice_decision(p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_tenant uuid := (p->>'tenant_id')::uuid;
  v_call_id bigint;
  v_conv bigint;
  v_from text := COALESCE(p->>'from', '');
BEGIN
  IF COALESCE(p->>'kind', '') = 'dial_status' AND COALESCE(p->>'call_sid', '') <> '' THEN
    UPDATE mctb.calls
       SET dial_status = COALESCE(p->>'dial_status', ''),
           missed = COALESCE((p->>'missed')::boolean, false),
           text_sent = COALESCE((p->>'text_sent')::boolean, false),
           suppressed_reason = NULLIF(p->>'suppressed_reason', '')
     WHERE tenant_id = v_tenant AND call_sid = p->>'call_sid'
     RETURNING id INTO v_call_id;
  END IF;

  IF v_call_id IS NULL THEN
    INSERT INTO mctb.calls (tenant_id, call_sid, from_number, to_number, dial_status, missed, text_sent, suppressed_reason)
    VALUES (
      v_tenant,
      COALESCE(p->>'call_sid', ''),
      v_from,
      COALESCE(p->>'to', ''),
      COALESCE(p->>'dial_status', ''),
      COALESCE((p->>'missed')::boolean, false),
      COALESCE((p->>'text_sent')::boolean, false),
      NULLIF(p->>'suppressed_reason', '')
    )
    ON CONFLICT (tenant_id, call_sid) WHERE call_sid <> '' DO NOTHING
    RETURNING id INTO v_call_id;
    IF v_call_id IS NULL AND COALESCE(p->>'call_sid', '') <> '' THEN
      RETURN jsonb_build_object('duplicate', true);
    END IF;
  END IF;

  v_conv := mctb.upsert_conversation(v_tenant, v_from, p->'conversation');
  PERFORM mctb.store_outbound(v_tenant, p->'outbound', v_conv);

  IF p->'owner_notification' IS NOT NULL AND jsonb_typeof(p->'owner_notification') = 'object' THEN
    INSERT INTO mctb.owner_notifications (tenant_id, channel, body)
    VALUES (v_tenant, COALESCE(p->'owner_notification'->>'channel', 'sms'), p->'owner_notification'->>'body');
  END IF;

  RETURN jsonb_build_object('ok', true, 'call_id', v_call_id);
END;
$$;

CREATE OR REPLACE FUNCTION mctb.apply_sms_decision(p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_tenant uuid := (p->>'tenant_id')::uuid;
  v_from text := COALESCE(p->>'from', '');
  v_conv bigint;
  v_estimate_id bigint;
BEGIN
  IF COALESCE((p->>'suppress')::boolean, false) AND v_from <> '' THEN
    INSERT INTO mctb.suppressions (tenant_id, phone, reason)
    VALUES (v_tenant, v_from, 'stop')
    ON CONFLICT (tenant_id, phone) DO UPDATE SET reason = 'stop', created_at = now();
  END IF;

  IF COALESCE((p->>'clear_suppression')::boolean, false) AND v_from <> '' THEN
    DELETE FROM mctb.suppressions WHERE tenant_id = v_tenant AND phone = v_from;
  END IF;

  v_conv := mctb.upsert_conversation(v_tenant, CASE WHEN COALESCE(p->>'inbound_purpose', '') = 'dashboard' THEN '' ELSE v_from END, p->'conversation');

  IF p->>'inbound_body' IS NOT NULL AND p->>'inbound_purpose' IS DISTINCT FROM 'dashboard' THEN
    INSERT INTO mctb.messages (tenant_id, conversation_id, direction, from_number, to_number, body, twilio_sid, purpose)
    VALUES (
      v_tenant,
      v_conv,
      'in',
      v_from,
      COALESCE(p->>'to', ''),
      p->>'inbound_body',
      NULLIF(p->>'message_sid', ''),
      COALESCE(p->>'inbound_purpose', 'sms')
    );
  END IF;

  IF p->'new_estimate' IS NOT NULL AND jsonb_typeof(p->'new_estimate') = 'object' THEN
    INSERT INTO mctb.estimates (tenant_id, customer_name, phone, job, amount_cents, status, step_index, next_send_at)
    VALUES (
      v_tenant,
      p->'new_estimate'->>'customer_name',
      p->'new_estimate'->>'phone',
      p->'new_estimate'->>'job',
      (p->'new_estimate'->>'amount_cents')::integer,
      'open',
      0,
      NULLIF(p->'new_estimate'->>'next_send_at', '')::timestamptz
    )
    RETURNING id INTO v_estimate_id;
  END IF;

  IF p->'estimate_update' IS NOT NULL AND jsonb_typeof(p->'estimate_update') = 'object' THEN
    UPDATE mctb.estimates
       SET status = p->'estimate_update'->>'status',
           next_send_at = CASE
             WHEN p->'estimate_update'->>'status' IN ('won', 'lost', 'replied', 'opted_out', 'completed') THEN NULL
             ELSE next_send_at
           END,
           updated_at = now()
     WHERE id = (p->'estimate_update'->>'id')::bigint
       AND tenant_id = v_tenant;
  END IF;

  PERFORM mctb.store_outbound(v_tenant, p->'outbound', v_conv);

  IF p->'owner_notification' IS NOT NULL AND jsonb_typeof(p->'owner_notification') = 'object' THEN
    INSERT INTO mctb.owner_notifications (tenant_id, channel, body)
    VALUES (v_tenant, COALESCE(p->'owner_notification'->>'channel', 'sms'), COALESCE(p->'owner_notification'->>'body', ''));
  END IF;

  RETURN jsonb_build_object('ok', true, 'estimate_id', v_estimate_id);
END;
$$;

CREATE OR REPLACE FUNCTION mctb.due_work()
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT jsonb_build_object(
    'followups', COALESCE((
      SELECT jsonb_agg(x.obj ORDER BY x.next_send_at, x.estimate_id)
      FROM (
        SELECT e.next_send_at, e.id AS estimate_id, jsonb_build_object(
          'estimate_id', e.id,
          'tenant_id', e.tenant_id,
          'customer_name', e.customer_name,
          'phone', e.phone,
          'job', e.job,
          'amount_cents', e.amount_cents,
          'status', e.status,
          'step_index', e.step_index,
          'next_send_at', e.next_send_at,
          'created_at', e.created_at,
          'business_name', t.business_name,
          'twilio_number', t.twilio_number,
          'owner_name', t.owner_name,
          'owner_phone', t.owner_phone,
          'timezone', t.timezone,
          'hours_text', t.hours_text,
          'booking_link', t.booking_link,
          'quiet_start', t.quiet_start,
          'quiet_end', t.quiet_end,
          'daily_sms_limit', t.daily_sms_limit,
          'per_number_daily_limit', t.per_number_daily_limit,
          'followup_templates', to_jsonb(t.followup_templates),
          'suppressed', EXISTS (
            SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = e.tenant_id AND s.phone = e.phone
          ),
          'sent_today', mctb.sent_count(e.tenant_id, NULL),
          'sent_to_today', mctb.sent_count(e.tenant_id, e.phone)
        ) AS obj
        FROM mctb.estimates e
        JOIN mctb.tenants t ON t.id = e.tenant_id
        WHERE e.status = 'open'
          AND e.next_send_at IS NOT NULL
          AND e.next_send_at <= now()
          AND t.active
        ORDER BY e.next_send_at, e.id
        LIMIT 100
      ) x
    ), '[]'::jsonb),
    'queued', COALESCE((
      SELECT jsonb_agg(x.obj ORDER BY x.send_at, x.queue_id)
      FROM (
        SELECT q.send_at, q.id AS queue_id, jsonb_build_object(
          'queue_id', q.id,
          'tenant_id', q.tenant_id,
          'to_number', q.to_number,
          'from_number', q.from_number,
          'body', q.body,
          'purpose', q.purpose,
          'send_at', q.send_at,
          'twilio_number', t.twilio_number,
          'timezone', t.timezone,
          'quiet_start', t.quiet_start,
          'quiet_end', t.quiet_end,
          'daily_sms_limit', t.daily_sms_limit,
          'per_number_daily_limit', t.per_number_daily_limit,
          'suppressed', EXISTS (
            SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = q.tenant_id AND s.phone = q.to_number
          ),
          'sent_today', mctb.sent_count(q.tenant_id, NULL),
          'sent_to_today', mctb.sent_count(q.tenant_id, q.to_number)
        ) AS obj
        FROM mctb.outbound_queue q
        JOIN mctb.tenants t ON t.id = q.tenant_id
        WHERE q.status = 'pending' AND q.send_at <= now() AND t.active
        ORDER BY q.send_at
        LIMIT 100
      ) x
    ), '[]'::jsonb)
  );
$$;

CREATE OR REPLACE FUNCTION mctb.mark_outbound(p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_status text := COALESCE(p->>'status', '');
  v_tenant uuid := (p->>'tenant_id')::uuid;
BEGIN
  IF p->>'kind' = 'followup' THEN
    UPDATE mctb.estimates
       SET step_index = COALESCE((p->>'step_index')::integer, step_index),
           status = COALESCE(NULLIF(p->>'estimate_status', ''), status),
           next_send_at = CASE
             WHEN p->>'next_send_at' IS NULL OR p->>'next_send_at' = '' OR p->>'next_send_at' = 'null' THEN NULL
             ELSE (p->>'next_send_at')::timestamptz
           END,
           updated_at = now()
     WHERE id = (p->>'estimate_id')::bigint
       AND tenant_id = v_tenant;

    IF v_status IN ('sent', 'mock_sent') AND COALESCE(p->>'body', '') <> '' THEN
      INSERT INTO mctb.outbound_log (tenant_id, to_number, from_number, body, purpose, provider, provider_sid, status)
      VALUES (
        v_tenant,
        p->>'to',
        p->>'from',
        p->>'body',
        COALESCE(p->>'purpose', 'followup'),
        CASE WHEN v_status = 'sent' THEN 'twilio' ELSE 'mock' END,
        NULLIF(p->>'provider_sid', ''),
        v_status
      );
      INSERT INTO mctb.messages (tenant_id, direction, from_number, to_number, body, purpose)
      VALUES (v_tenant, 'out', COALESCE(p->>'from', ''), COALESCE(p->>'to', ''), p->>'body', COALESCE(p->>'purpose', 'followup'));
    END IF;
  ELSIF p->>'kind' = 'queue' THEN
    IF v_status IN ('sent', 'mock_sent', 'suppressed') THEN
      UPDATE mctb.outbound_queue
         SET status = CASE WHEN v_status = 'suppressed' THEN 'cancelled' ELSE 'sent' END
       WHERE id = (p->>'queue_id')::bigint
         AND tenant_id = v_tenant;
    ELSIF v_status IN ('deferred', 'rate_limited', 'retry') AND NULLIF(p->>'next_send_at', '') IS NOT NULL THEN
      UPDATE mctb.outbound_queue
         SET send_at = (p->>'next_send_at')::timestamptz
       WHERE id = (p->>'queue_id')::bigint
         AND tenant_id = v_tenant
         AND status = 'pending';
    END IF;

    IF v_status IN ('sent', 'mock_sent') AND COALESCE(p->>'body', '') <> '' THEN
      INSERT INTO mctb.outbound_log (tenant_id, to_number, from_number, body, purpose, provider, provider_sid, status)
      VALUES (
        v_tenant,
        p->>'to',
        p->>'from',
        p->>'body',
        COALESCE(p->>'purpose', ''),
        CASE WHEN v_status = 'sent' THEN 'twilio' ELSE 'mock' END,
        NULLIF(p->>'provider_sid', ''),
        v_status
      );
      INSERT INTO mctb.messages (tenant_id, direction, from_number, to_number, body, purpose)
      VALUES (v_tenant, 'out', COALESCE(p->>'from', ''), COALESCE(p->>'to', ''), p->>'body', COALESCE(p->>'purpose', ''));
    END IF;
  END IF;

  RETURN jsonb_build_object('ok', true);
END;
$$;

CREATE OR REPLACE FUNCTION mctb.dashboard_snapshot(p_token text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE dashboard_token = p_token AND active LIMIT 1;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  RETURN jsonb_build_object(
    'tenant', mctb.tenant_public(t),
    'stats', jsonb_build_object(
      'missed_calls_7d', (SELECT count(*) FROM mctb.calls c WHERE c.tenant_id = t.id AND c.missed AND c.created_at > now() - interval '7 days'),
      'missed_calls_30d', (SELECT count(*) FROM mctb.calls c WHERE c.tenant_id = t.id AND c.missed AND c.created_at > now() - interval '30 days'),
      'recovered_7d', (
        SELECT count(*) FROM mctb.calls c
        WHERE c.tenant_id = t.id AND c.missed AND c.created_at > now() - interval '7 days'
          AND EXISTS (
            SELECT 1 FROM mctb.messages m
            WHERE m.tenant_id = c.tenant_id AND m.direction = 'in' AND m.from_number = c.from_number AND m.created_at >= c.created_at
          )
      ),
      'recovered_30d', (
        SELECT count(*) FROM mctb.calls c
        WHERE c.tenant_id = t.id AND c.missed AND c.created_at > now() - interval '30 days'
          AND EXISTS (
            SELECT 1 FROM mctb.messages m
            WHERE m.tenant_id = c.tenant_id AND m.direction = 'in' AND m.from_number = c.from_number AND m.created_at >= c.created_at
          )
      ),
      'leads_30d', (
        SELECT count(*) FROM mctb.conversations c
        WHERE c.tenant_id = t.id
          AND c.qualified_at IS NOT NULL
          AND c.qualified_at > now() - interval '30 days'
      ),
      'texts_30d', (
        SELECT count(*) FROM mctb.outbound_log o
        WHERE o.tenant_id = t.id AND o.status IN ('twiml', 'sent', 'mock_sent') AND o.created_at > now() - interval '30 days'
      ),
      'estimates_open', (SELECT count(*) FROM mctb.estimates e WHERE e.tenant_id = t.id AND e.status = 'open'),
      'jobs_won_30d', (
        SELECT count(*) FROM mctb.estimates e
        WHERE e.tenant_id = t.id AND e.status = 'won' AND e.updated_at > now() - interval '30 days'
      ),
      'won_cents_30d', (
        SELECT COALESCE(sum(e.amount_cents), 0) FROM mctb.estimates e
        WHERE e.tenant_id = t.id AND e.status = 'won' AND e.updated_at > now() - interval '30 days'
      ),
      'opt_outs', (SELECT count(*) FROM mctb.suppressions s WHERE s.tenant_id = t.id)
    ),
    'calls', COALESCE((
      SELECT jsonb_agg(x.obj) FROM (
        SELECT jsonb_build_object(
          'from_number', c.from_number, 'missed', c.missed, 'text_sent', c.text_sent,
          'suppressed_reason', c.suppressed_reason, 'dial_status', c.dial_status, 'created_at', c.created_at
        ) AS obj
        FROM mctb.calls c WHERE c.tenant_id = t.id ORDER BY c.id DESC LIMIT 25
      ) x
    ), '[]'::jsonb),
    'conversations', COALESCE((
      SELECT jsonb_agg(x.obj) FROM (
        SELECT jsonb_build_object(
          'phone', c.phone, 'state', c.state, 'need', c.need, 'location', c.location,
          'urgency', c.urgency, 'updated_at', c.updated_at
        ) AS obj
        FROM mctb.conversations c WHERE c.tenant_id = t.id ORDER BY c.updated_at DESC LIMIT 25
      ) x
    ), '[]'::jsonb),
    'estimates', COALESCE((
      SELECT jsonb_agg(x.obj) FROM (
        SELECT jsonb_build_object(
          'id', e.id, 'customer_name', e.customer_name, 'phone', e.phone, 'job', e.job,
          'amount_cents', e.amount_cents, 'status', e.status, 'step_index', e.step_index,
          'next_send_at', e.next_send_at, 'created_at', e.created_at
        ) AS obj
        FROM mctb.estimates e WHERE e.tenant_id = t.id ORDER BY e.id DESC LIMIT 25
      ) x
    ), '[]'::jsonb),
    'notifications', COALESCE((
      SELECT jsonb_agg(x.obj) FROM (
        SELECT jsonb_build_object('body', n.body, 'channel', n.channel, 'created_at', n.created_at) AS obj
        FROM mctb.owner_notifications n WHERE n.tenant_id = t.id ORDER BY n.id DESC LIMIT 25
      ) x
    ), '[]'::jsonb)
  );
END;
$$;
