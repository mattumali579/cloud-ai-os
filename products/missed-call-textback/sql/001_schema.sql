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

ALTER TABLE mctb.tenants ADD COLUMN IF NOT EXISTS owner_daily_sms_limit integer NOT NULL DEFAULT 60 CHECK (owner_daily_sms_limit > 0);

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
    'owner_daily_sms_limit', t.owner_daily_sms_limit,
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
    'dashboard_path', '/webhook/mctb-dashboard?token=' || t.dashboard_token,
    'owner_cap_notice_sent', EXISTS (
      SELECT 1 FROM mctb.outbound_log o
      WHERE o.tenant_id = t.id
        AND o.purpose = 'owner_cap_notice'
        AND o.status IN ('twiml', 'sent', 'mock_sent')
        AND o.created_at > now() - interval '24 hours'
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
    'dashboard_path', '/webhook/mctb-dashboard?token=' || t.dashboard_token,
    'owner_cap_notice_sent', EXISTS (
      SELECT 1 FROM mctb.outbound_log o
      WHERE o.tenant_id = t.id
        AND o.purpose = 'owner_cap_notice'
        AND o.status IN ('twiml', 'sent', 'mock_sent')
        AND o.created_at > now() - interval '24 hours'
    ),
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
  v_updated integer := 0;
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

-- Revenue reports, Google review requests, and health checks.
-- Additive: re-running this file on an existing database keeps old rows.

ALTER TABLE mctb.tenants ADD COLUMN IF NOT EXISTS revenue_report_enabled boolean NOT NULL DEFAULT true;
ALTER TABLE mctb.tenants ADD COLUMN IF NOT EXISTS google_review_url text;

CREATE TABLE IF NOT EXISTS mctb.revenue_reports (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  period text NOT NULL CHECK (period IN ('week', 'month')),
  period_key text NOT NULL,
  status text NOT NULL,
  body text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, period, period_key)
);

CREATE TABLE IF NOT EXISTS mctb.review_requests (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  phone text NOT NULL,
  token text NOT NULL UNIQUE,
  estimate_id bigint,
  status text NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled', 'sent', 'reminded', 'clicked', 'suppressed', 'skipped')),
  send_at timestamptz,
  reminder_at timestamptz,
  sent_at timestamptz,
  reminded_at timestamptz,
  clicked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS review_requests_due
  ON mctb.review_requests (status, send_at);

CREATE INDEX IF NOT EXISTS review_requests_phone
  ON mctb.review_requests (tenant_id, phone, created_at DESC);

CREATE TABLE IF NOT EXISTS mctb.workflow_heartbeats (
  workflow text PRIMARY KEY,
  ran_at timestamptz NOT NULL DEFAULT now(),
  detail text NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mctb.health_state (
  id integer PRIMARY KEY DEFAULT 1,
  status text NOT NULL DEFAULT 'ok',
  alert_open boolean NOT NULL DEFAULT false,
  last_alert_at timestamptz,
  last_detail text NOT NULL DEFAULT '',
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT health_state_one CHECK (id = 1)
);

INSERT INTO mctb.health_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

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
    'owner_daily_sms_limit', t.owner_daily_sms_limit,
    'revenue_report_enabled', t.revenue_report_enabled,
    'google_review_url', t.google_review_url,
    'active', t.active
  );
$$;

-- Counts for one half-open window [p_start, p_end).
-- recovered_revenue_cents is won dollars whose customer was texted after a
-- missed call and replied before the job was marked won.
CREATE OR REPLACE FUNCTION mctb.period_stats(p_tenant uuid, p_start timestamptz, p_end timestamptz)
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT jsonb_build_object(
    'missed_calls', (
      SELECT count(*) FROM mctb.calls c
      WHERE c.tenant_id = p_tenant AND c.missed
        AND c.created_at >= p_start AND c.created_at < p_end
    ),
    'callers_texted', (
      SELECT count(DISTINCT c.from_number) FROM mctb.calls c
      WHERE c.tenant_id = p_tenant AND c.missed AND c.text_sent AND c.from_number <> ''
        AND c.created_at >= p_start AND c.created_at < p_end
    ),
    'recovered', (
      SELECT count(DISTINCT c.from_number) FROM mctb.calls c
      WHERE c.tenant_id = p_tenant AND c.missed AND c.from_number <> ''
        AND c.created_at >= p_start AND c.created_at < p_end
        AND EXISTS (
          SELECT 1 FROM mctb.messages m
          WHERE m.tenant_id = c.tenant_id AND m.direction = 'in'
            AND m.from_number = c.from_number AND m.created_at >= c.created_at
        )
    ),
    'qualified_leads', (
      SELECT count(*) FROM mctb.conversations c
      WHERE c.tenant_id = p_tenant AND c.qualified_at IS NOT NULL
        AND c.qualified_at >= p_start AND c.qualified_at < p_end
    ),
    'estimates_sent', (
      SELECT count(*) FROM mctb.estimates e
      WHERE e.tenant_id = p_tenant
        AND e.created_at >= p_start AND e.created_at < p_end
    ),
    'jobs_won', (
      SELECT count(*) FROM mctb.estimates e
      WHERE e.tenant_id = p_tenant AND e.status = 'won'
        AND e.updated_at >= p_start AND e.updated_at < p_end
    ),
    'won_cents', (
      SELECT COALESCE(sum(e.amount_cents), 0) FROM mctb.estimates e
      WHERE e.tenant_id = p_tenant AND e.status = 'won'
        AND e.updated_at >= p_start AND e.updated_at < p_end
    ),
    'recovered_revenue_cents', (
      SELECT COALESCE(sum(e.amount_cents), 0) FROM mctb.estimates e
      WHERE e.tenant_id = p_tenant AND e.status = 'won'
        AND e.updated_at >= p_start AND e.updated_at < p_end
        AND EXISTS (
          SELECT 1 FROM mctb.calls c
          WHERE c.tenant_id = e.tenant_id AND c.missed AND c.text_sent
            AND c.from_number = e.phone AND c.created_at <= e.updated_at
            AND EXISTS (
              SELECT 1 FROM mctb.messages m
              WHERE m.tenant_id = c.tenant_id AND m.direction = 'in'
                AND m.from_number = c.from_number
                AND m.created_at >= c.created_at AND m.created_at <= e.updated_at
            )
        )
    )
  );
$$;

CREATE OR REPLACE FUNCTION mctb.schedule_review(p_tenant uuid, p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
  v_phone text := COALESCE(p->>'phone', '');
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE id = p_tenant;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'tenant');
  END IF;
  IF t.google_review_url IS NULL OR btrim(t.google_review_url) = '' OR t.google_review_url !~* '^https://' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'no_url');
  END IF;
  IF v_phone = '' THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'phone');
  END IF;
  IF EXISTS (
    SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = p_tenant AND s.phone = v_phone
  ) THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'opted_out');
  END IF;
  IF EXISTS (
    SELECT 1 FROM mctb.review_requests r
    WHERE r.tenant_id = p_tenant AND r.phone = v_phone
      AND r.created_at > now() - interval '90 days'
      AND r.status <> 'skipped'
  ) THEN
    RETURN jsonb_build_object('ok', false, 'reason', 'recent');
  END IF;
  INSERT INTO mctb.review_requests (tenant_id, phone, token, estimate_id, status, send_at)
  VALUES (
    p_tenant,
    v_phone,
    replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', ''),
    NULLIF(p->>'estimate_id', '')::bigint,
    'scheduled',
    COALESCE(NULLIF(p->>'send_at', '')::timestamptz, now() + interval '2 hours')
  );
  RETURN jsonb_build_object('ok', true, 'reason', 'scheduled');
END;
$$;

CREATE OR REPLACE FUNCTION mctb.review_click(p_token text)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  r mctb.review_requests%ROWTYPE;
  t mctb.tenants%ROWTYPE;
BEGIN
  SELECT * INTO r FROM mctb.review_requests WHERE token = COALESCE(p_token, '');
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', false);
  END IF;
  SELECT * INTO t FROM mctb.tenants WHERE id = r.tenant_id;
  IF NOT FOUND OR t.google_review_url IS NULL OR t.google_review_url !~* '^https://' THEN
    RETURN jsonb_build_object('ok', false);
  END IF;
  UPDATE mctb.review_requests
     SET clicked_at = COALESCE(clicked_at, now()),
         status = 'clicked'
   WHERE id = r.id;
  RETURN jsonb_build_object('ok', true, 'location', t.google_review_url);
END;
$$;

CREATE OR REPLACE FUNCTION mctb.stamp_heartbeat(p_workflow text, p_detail text DEFAULT '')
RETURNS jsonb
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO mctb.workflow_heartbeats (workflow, ran_at, detail)
  VALUES (p_workflow, now(), COALESCE(p_detail, ''))
  ON CONFLICT (workflow) DO UPDATE
    SET ran_at = now(), detail = COALESCE(p_detail, '');
  RETURN jsonb_build_object('ok', true, 'workflow', p_workflow);
END;
$$;

CREATE OR REPLACE FUNCTION mctb.service_ages()
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT jsonb_build_object(
    'db', 'ok',
    'active_tenants', (SELECT count(*) FROM mctb.tenants WHERE active),
    'oldest_tenant_age_seconds', (
      SELECT EXTRACT(EPOCH FROM (now() - min(created_at)))::integer
      FROM mctb.tenants WHERE active
    ),
    'followups_age_seconds', (
      SELECT EXTRACT(EPOCH FROM (now() - ran_at))::integer
      FROM mctb.workflow_heartbeats WHERE workflow = 'followups'
    ),
    'revenue_age_seconds', (
      SELECT EXTRACT(EPOCH FROM (now() - ran_at))::integer
      FROM mctb.workflow_heartbeats WHERE workflow = 'revenue'
    )
  );
$$;

CREATE OR REPLACE FUNCTION mctb.heartbeat()
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT mctb.service_ages();
$$;

CREATE OR REPLACE FUNCTION mctb.health_snapshot(p_admin_phone text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
  v_phone text := COALESCE(p_admin_phone, '');
  ages jsonb;
BEGIN
  ages := mctb.service_ages();
  SELECT * INTO t FROM mctb.tenants WHERE active ORDER BY created_at, id LIMIT 1;
  RETURN ages || jsonb_build_object(
    'state', (
      SELECT jsonb_build_object(
        'status', s.status,
        'alert_open', s.alert_open,
        'last_alert_at', s.last_alert_at,
        'last_detail', s.last_detail
      )
      FROM mctb.health_state s WHERE s.id = 1
    ),
    'sender', CASE WHEN t.id IS NULL THEN NULL ELSE jsonb_build_object(
      'tenant_id', t.id,
      'twilio_number', t.twilio_number,
      'timezone', t.timezone,
      'quiet_start', t.quiet_start,
      'quiet_end', t.quiet_end,
      'suppressed', EXISTS (
        SELECT 1 FROM mctb.suppressions sup
        WHERE sup.tenant_id = t.id AND sup.phone = v_phone AND v_phone <> ''
      )
    ) END
  );
END;
$$;

CREATE OR REPLACE FUNCTION mctb.record_health_alert(p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_sms jsonb := p->'sms';
  v_tenant uuid;
BEGIN
  INSERT INTO mctb.health_state (id, status, alert_open, last_alert_at, last_detail, updated_at)
  VALUES (
    1,
    COALESCE(p->>'status', 'ok'),
    COALESCE((p->>'alert_open')::boolean, false),
    NULLIF(p->>'last_alert_at', '')::timestamptz,
    COALESCE(p->>'detail', ''),
    now()
  )
  ON CONFLICT (id) DO UPDATE SET
    status = EXCLUDED.status,
    alert_open = EXCLUDED.alert_open,
    last_alert_at = EXCLUDED.last_alert_at,
    last_detail = EXCLUDED.last_detail,
    updated_at = now();

  IF v_sms IS NOT NULL AND jsonb_typeof(v_sms) = 'object' AND COALESCE(v_sms->>'to', '') <> '' THEN
    v_tenant := (v_sms->>'tenant_id')::uuid;
    IF NOT EXISTS (
      SELECT 1 FROM mctb.suppressions s
      WHERE s.tenant_id = v_tenant AND s.phone = v_sms->>'to'
    ) THEN
      IF v_sms->>'delivery' = 'queue' THEN
        INSERT INTO mctb.outbound_queue (tenant_id, to_number, from_number, body, purpose, send_at, status)
        VALUES (
          v_tenant,
          v_sms->>'to',
          v_sms->>'from',
          COALESCE(v_sms->>'body', ''),
          COALESCE(v_sms->>'purpose', 'health_alert'),
          COALESCE(NULLIF(v_sms->>'send_at', '')::timestamptz, now()),
          'pending'
        );
      ELSE
        INSERT INTO mctb.outbound_log (tenant_id, to_number, from_number, body, purpose, provider, status)
        VALUES (
          v_tenant,
          v_sms->>'to',
          v_sms->>'from',
          COALESCE(v_sms->>'body', ''),
          COALESCE(v_sms->>'purpose', 'health_alert'),
          CASE WHEN v_sms->>'delivery' = 'twilio' THEN 'twilio' ELSE 'mock' END,
          CASE WHEN v_sms->>'delivery' = 'twilio' THEN 'sent' ELSE 'mock_sent' END
        );
      END IF;
    END IF;
  END IF;
  RETURN jsonb_build_object('ok', true);
END;
$$;

CREATE OR REPLACE FUNCTION mctb.due_revenue_reports(p_now timestamptz)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  result jsonb := '[]'::jsonb;
  t mctb.tenants%ROWTYPE;
  local_ts timestamp;
  week_start timestamp;
  week_end timestamp;
  month_start timestamp;
  week_key text;
  month_key text;
BEGIN
  FOR t IN
    SELECT * FROM mctb.tenants
    WHERE active AND revenue_report_enabled
    ORDER BY created_at, id
  LOOP
    local_ts := p_now AT TIME ZONE t.timezone;
    IF EXTRACT(DOW FROM local_ts) = 1 AND local_ts::time >= time '08:00' THEN
      week_end := date_trunc('week', local_ts);
      week_start := week_end - interval '7 days';
      week_key := to_char(week_start::date, 'IYYY-"W"IW');
      IF NOT EXISTS (
        SELECT 1 FROM mctb.revenue_reports r
        WHERE r.tenant_id = t.id AND r.period = 'week' AND r.period_key = week_key
      ) THEN
        result := result || jsonb_build_array(jsonb_build_object(
          'period', 'week',
          'period_key', week_key,
          'tenant_id', t.id,
          'business_name', t.business_name,
          'timezone', t.timezone,
          'quiet_start', t.quiet_start,
          'quiet_end', t.quiet_end,
          'owner_phone', t.owner_phone,
          'twilio_number', t.twilio_number,
          'dashboard_token', t.dashboard_token,
          'owner_daily_sms_limit', t.owner_daily_sms_limit,
          'daily_sms_limit', t.daily_sms_limit,
          'suppressed', EXISTS (
            SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = t.id AND s.phone = t.owner_phone
          ),
          'stats', mctb.period_stats(
            t.id,
            week_start AT TIME ZONE t.timezone,
            week_end AT TIME ZONE t.timezone
          )
        ));
      END IF;
    END IF;

    IF local_ts::date = (date_trunc('month', local_ts) + interval '1 month' - interval '1 day')::date
       AND local_ts::time >= time '08:00' THEN
      month_start := date_trunc('month', local_ts);
      month_key := to_char(local_ts, 'YYYY-MM');
      IF NOT EXISTS (
        SELECT 1 FROM mctb.revenue_reports r
        WHERE r.tenant_id = t.id AND r.period = 'month' AND r.period_key = month_key
      ) THEN
        result := result || jsonb_build_array(jsonb_build_object(
          'period', 'month',
          'period_key', month_key,
          'tenant_id', t.id,
          'business_name', t.business_name,
          'timezone', t.timezone,
          'quiet_start', t.quiet_start,
          'quiet_end', t.quiet_end,
          'owner_phone', t.owner_phone,
          'twilio_number', t.twilio_number,
          'dashboard_token', t.dashboard_token,
          'owner_daily_sms_limit', t.owner_daily_sms_limit,
          'daily_sms_limit', t.daily_sms_limit,
          'suppressed', EXISTS (
            SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = t.id AND s.phone = t.owner_phone
          ),
          'stats', mctb.period_stats(
            t.id,
            month_start AT TIME ZONE t.timezone,
            p_now
          )
        ));
      END IF;
    END IF;
  END LOOP;
  RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION mctb.apply_revenue_report(p jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $$
DECLARE
  v_tenant uuid := (p->>'tenant_id')::uuid;
  v_inserted integer := 0;
BEGIN
  INSERT INTO mctb.revenue_reports (tenant_id, period, period_key, status, body)
  VALUES (
    v_tenant,
    p->>'period',
    p->>'period_key',
    COALESCE(p->>'status', 'queued'),
    COALESCE(p->>'body', '')
  )
  ON CONFLICT (tenant_id, period, period_key) DO NOTHING;
  GET DIAGNOSTICS v_inserted = ROW_COUNT;
  IF v_inserted = 0 THEN
    RETURN jsonb_build_object('ok', true, 'duplicate', true);
  END IF;
  IF COALESCE(p->>'status', '') = 'queued'
     AND COALESCE(p->>'body', '') <> ''
     AND COALESCE(p->>'to', '') <> '' THEN
    INSERT INTO mctb.outbound_queue (tenant_id, to_number, from_number, body, purpose, send_at, status)
    VALUES (
      v_tenant,
      p->>'to',
      p->>'from',
      p->>'body',
      COALESCE(p->>'purpose', 'revenue_report'),
      COALESCE(NULLIF(p->>'send_at', '')::timestamptz, now()),
      'pending'
    );
  END IF;
  RETURN jsonb_build_object('ok', true, 'duplicate', false);
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
    'recent_review_phones', COALESCE((
      SELECT jsonb_agg(DISTINCT r.phone)
      FROM mctb.review_requests r
      WHERE r.tenant_id = t.id
        AND r.created_at > now() - interval '90 days'
        AND r.status <> 'skipped'
    ), '[]'::jsonb),
    'dashboard_path', '/webhook/mctb-dashboard?token=' || t.dashboard_token,
    'owner_cap_notice_sent', EXISTS (
      SELECT 1 FROM mctb.outbound_log o
      WHERE o.tenant_id = t.id
        AND o.purpose = 'owner_cap_notice'
        AND o.status IN ('twiml', 'sent', 'mock_sent')
        AND o.created_at > now() - interval '24 hours'
    ),
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
    'recent_review_phones', COALESCE((
      SELECT jsonb_agg(DISTINCT r.phone)
      FROM mctb.review_requests r
      WHERE r.tenant_id = t.id
        AND r.created_at > now() - interval '90 days'
        AND r.status <> 'skipped'
    ), '[]'::jsonb),
    'sent_today', mctb.sent_count(t.id, NULL),
    'sent_to_today', 0,
    'sent_to_owner_today', mctb.sent_count(t.id, t.owner_phone)
  );
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

  IF p->'review_request' IS NOT NULL AND jsonb_typeof(p->'review_request') = 'object' THEN
    PERFORM mctb.schedule_review(v_tenant, p->'review_request');
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
          'owner_daily_sms_limit', t.owner_daily_sms_limit,
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
    ), '[]'::jsonb),
    'reviews', COALESCE((
      SELECT jsonb_agg(x.obj ORDER BY x.due_at, x.review_id)
      FROM (
        SELECT
          CASE WHEN r.status = 'scheduled' THEN r.send_at ELSE r.reminder_at END AS due_at,
          r.id AS review_id,
          jsonb_build_object(
            'review_id', r.id,
            'tenant_id', r.tenant_id,
            'phone', r.phone,
            'token', r.token,
            'status', r.status,
            'kind', CASE WHEN r.status = 'scheduled' THEN 'request' ELSE 'reminder' END,
            'google_review_url', t.google_review_url,
            'business_name', t.business_name,
            'twilio_number', t.twilio_number,
            'timezone', t.timezone,
            'quiet_start', t.quiet_start,
            'quiet_end', t.quiet_end,
            'daily_sms_limit', t.daily_sms_limit,
            'per_number_daily_limit', t.per_number_daily_limit,
            'suppressed', EXISTS (
              SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = r.tenant_id AND s.phone = r.phone
            ),
            'sent_today', mctb.sent_count(r.tenant_id, NULL),
            'sent_to_today', mctb.sent_count(r.tenant_id, r.phone)
          ) AS obj
        FROM mctb.review_requests r
        JOIN mctb.tenants t ON t.id = r.tenant_id
        WHERE t.active
          AND t.google_review_url ~* '^https://'
          AND (
            (r.status = 'scheduled' AND r.send_at IS NOT NULL AND r.send_at <= now())
            OR (
              r.status = 'sent'
              AND r.clicked_at IS NULL
              AND r.reminded_at IS NULL
              AND r.reminder_at IS NOT NULL
              AND r.reminder_at <= now()
            )
          )
        ORDER BY 1, r.id
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
  v_updated integer := 0;
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
  ELSIF p->>'kind' = 'review' THEN
    IF v_status IN ('deferred', 'rate_limited', 'retry') AND NULLIF(p->>'next_send_at', '') IS NOT NULL THEN
      UPDATE mctb.review_requests
         SET send_at = CASE WHEN status = 'scheduled' THEN (p->>'next_send_at')::timestamptz ELSE send_at END,
             reminder_at = CASE WHEN status <> 'scheduled' THEN (p->>'next_send_at')::timestamptz ELSE reminder_at END
       WHERE id = (p->>'review_id')::bigint
         AND tenant_id = v_tenant;
    ELSIF v_status IN ('suppressed', 'skipped') THEN
      UPDATE mctb.review_requests
         SET status = v_status
       WHERE id = (p->>'review_id')::bigint
         AND tenant_id = v_tenant
         AND status IN ('scheduled', 'sent');
    ELSIF v_status IN ('sent', 'mock_sent') AND COALESCE(p->>'body', '') <> '' THEN
      UPDATE mctb.review_requests
         SET status = CASE WHEN status = 'scheduled' THEN 'sent' WHEN status = 'sent' THEN 'reminded' ELSE status END,
             sent_at = CASE WHEN status = 'scheduled' THEN now() ELSE sent_at END,
             reminded_at = CASE WHEN status = 'sent' THEN now() ELSE reminded_at END,
             reminder_at = CASE WHEN status = 'scheduled' THEN now() + interval '3 days' ELSE reminder_at END
       WHERE id = (p->>'review_id')::bigint
         AND tenant_id = v_tenant
         AND clicked_at IS NULL
         AND status IN ('scheduled', 'sent');
      GET DIAGNOSTICS v_updated = ROW_COUNT;
      IF v_updated > 0 THEN
        INSERT INTO mctb.outbound_log (tenant_id, to_number, from_number, body, purpose, provider, provider_sid, status)
        VALUES (
          v_tenant,
          p->>'to',
          p->>'from',
          p->>'body',
          COALESCE(p->>'purpose', 'review_request'),
          CASE WHEN v_status = 'sent' THEN 'twilio' ELSE 'mock' END,
          NULLIF(p->>'provider_sid', ''),
          v_status
        );
        INSERT INTO mctb.messages (tenant_id, direction, from_number, to_number, body, purpose)
        VALUES (v_tenant, 'out', COALESCE(p->>'from', ''), COALESCE(p->>'to', ''), p->>'body', COALESCE(p->>'purpose', 'review_request'));
      END IF;
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
  week_start timestamptz;
  month_start timestamptz;
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE dashboard_token = p_token AND active LIMIT 1;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  week_start := date_trunc('week', (now() AT TIME ZONE t.timezone)) AT TIME ZONE t.timezone;
  month_start := date_trunc('month', (now() AT TIME ZONE t.timezone)) AT TIME ZONE t.timezone;
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
      'opt_outs', (SELECT count(*) FROM mctb.suppressions s WHERE s.tenant_id = t.id),
      'reviews_sent', (
        SELECT count(*) FROM mctb.review_requests r
        WHERE r.tenant_id = t.id AND r.sent_at IS NOT NULL
      ),
      'reviews_clicked', (
        SELECT count(*) FROM mctb.review_requests r
        WHERE r.tenant_id = t.id AND r.clicked_at IS NOT NULL
      ),
      'periods', jsonb_build_object(
        'week', mctb.period_stats(t.id, week_start, now() + interval '1 minute'),
        'month', mctb.period_stats(t.id, month_start, now() + interval '1 minute')
      )
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
