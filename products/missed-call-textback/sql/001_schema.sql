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
  qualify_done_template text NOT NULL DEFAULT 'This looks like a job we can help with. Reply with your preferred day or arrival window and {owner_name} will confirm it. Book online: {booking_link}',
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
  lead_token text UNIQUE NOT NULL DEFAULT md5(random()::text || clock_timestamp()::text),
  qualify_name_prompt text NOT NULL DEFAULT 'Thanks — what name should we put on the request?',
  qualify_unfit_template text NOT NULL DEFAULT 'Thanks for the details. {owner_name} will review the request and let you know whether {business_name} can help.',
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
  stage text NOT NULL DEFAULT 'new' CHECK (stage IN ('new', 'contacted', 'replied', 'qualified', 'booked', 'won', 'lost')),
  source text NOT NULL DEFAULT 'sms',
  customer_name text,
  email text,
  need text,
  location text,
  urgency text,
  qualification_status text NOT NULL DEFAULT 'pending' CHECK (qualification_status IN ('pending', 'qualified', 'unqualified')),
  qualification_score integer NOT NULL DEFAULT 0,
  requested_window text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, phone)
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

CREATE UNIQUE INDEX IF NOT EXISTS messages_twilio_sid_unique
  ON mctb.messages (tenant_id, twilio_sid)
  WHERE twilio_sid IS NOT NULL AND twilio_sid <> '';

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

CREATE TABLE IF NOT EXISTS mctb.appointments (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  conversation_id bigint REFERENCES mctb.conversations(id),
  customer_name text NOT NULL DEFAULT '',
  phone text NOT NULL,
  requested_window text NOT NULL,
  status text NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'confirmed', 'completed', 'cancelled')),
  source text NOT NULL DEFAULT 'sms',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS appointments_tenant_created
  ON mctb.appointments (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mctb.web_lead_events (
  id bigserial PRIMARY KEY,
  tenant_id uuid NOT NULL REFERENCES mctb.tenants(id),
  external_id text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, external_id)
);

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
  attempts integer NOT NULL DEFAULT 0,
  last_error text,
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
    'qualify_name_prompt', t.qualify_name_prompt,
    'qualify_location_prompt', t.qualify_location_prompt,
    'qualify_urgency_prompt', t.qualify_urgency_prompt,
    'qualify_done_template', t.qualify_done_template,
    'qualify_unfit_template', t.qualify_unfit_template,
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
      SELECT jsonb_build_object(
        'state', c.state, 'stage', c.stage, 'source', c.source, 'customer_name', c.customer_name,
        'email', c.email, 'need', c.need, 'location', c.location, 'urgency', c.urgency,
        'qualification_status', c.qualification_status, 'qualification_score', c.qualification_score,
        'requested_window', c.requested_window
      )
      FROM mctb.conversations c
      WHERE c.tenant_id = t.id AND c.phone = p_from
    ),
    'sent_today', mctb.sent_count(t.id, NULL),
    'sent_to_today', mctb.sent_count(t.id, p_from),
    'sent_to_owner_today', mctb.sent_count(t.id, t.owner_phone)
  );
END;
$$;

CREATE OR REPLACE FUNCTION mctb.load_sms_context(p_to text, p_from text, p_message_sid text DEFAULT '')
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
      SELECT 1 FROM mctb.messages m
      WHERE m.tenant_id = t.id AND m.twilio_sid = COALESCE(p_message_sid, '') AND COALESCE(p_message_sid, '') <> ''
    ),
    'suppressed', EXISTS (
      SELECT 1 FROM mctb.suppressions s WHERE s.tenant_id = t.id AND s.phone = p_from
    ),
    'conversation', (
      SELECT jsonb_build_object(
        'state', c.state, 'stage', c.stage, 'source', c.source, 'customer_name', c.customer_name,
        'email', c.email, 'need', c.need, 'location', c.location, 'urgency', c.urgency,
        'qualification_status', c.qualification_status, 'qualification_score', c.qualification_score,
        'requested_window', c.requested_window
      )
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

CREATE OR REPLACE FUNCTION mctb.web_lead_context(p_token text, p_external_id text)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  t mctb.tenants%ROWTYPE;
BEGIN
  SELECT * INTO t FROM mctb.tenants WHERE lead_token = p_token AND active LIMIT 1;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('tenant', NULL);
  END IF;
  RETURN jsonb_build_object(
    'tenant', mctb.tenant_public(t),
    'duplicate', COALESCE(p_external_id, '') <> '' AND EXISTS (
      SELECT 1 FROM mctb.web_lead_events e
      WHERE e.tenant_id = t.id AND e.external_id = p_external_id
    ),
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
  INSERT INTO mctb.conversations (
    tenant_id, phone, state, stage, source, customer_name, email, need, location, urgency,
    qualification_status, qualification_score, requested_window
  )
  VALUES (
    p_tenant,
    p_phone,
    COALESCE(p_conversation->>'state', 'awaiting_need'),
    COALESCE(NULLIF(p_conversation->>'stage', ''), 'contacted'),
    COALESCE(NULLIF(p_conversation->>'source', ''), 'sms'),
    NULLIF(p_conversation->>'customer_name', ''),
    NULLIF(p_conversation->>'email', ''),
    NULLIF(p_conversation->>'need', ''),
    NULLIF(p_conversation->>'location', ''),
    NULLIF(p_conversation->>'urgency', ''),
    COALESCE(NULLIF(p_conversation->>'qualification_status', ''), 'pending'),
    COALESCE((p_conversation->>'qualification_score')::integer, 0),
    NULLIF(p_conversation->>'requested_window', '')
  )
  ON CONFLICT (tenant_id, phone) DO UPDATE SET
    state = EXCLUDED.state,
    stage = EXCLUDED.stage,
    source = COALESCE(EXCLUDED.source, mctb.conversations.source),
    customer_name = COALESCE(EXCLUDED.customer_name, mctb.conversations.customer_name),
    email = COALESCE(EXCLUDED.email, mctb.conversations.email),
    need = COALESCE(EXCLUDED.need, mctb.conversations.need),
    location = COALESCE(EXCLUDED.location, mctb.conversations.location),
    urgency = COALESCE(EXCLUDED.urgency, mctb.conversations.urgency),
    qualification_status = EXCLUDED.qualification_status,
    qualification_score = EXCLUDED.qualification_score,
    requested_window = COALESCE(EXCLUDED.requested_window, mctb.conversations.requested_window),
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

  IF COALESCE((p->>'text_sent')::boolean, false) AND v_from <> '' THEN
    INSERT INTO mctb.outbound_queue (tenant_id, to_number, from_number, body, purpose, send_at, status)
    VALUES
      (v_tenant, v_from, COALESCE(p->>'to', ''), 'Just checking that you saw our text — what service or job do you need help with? Reply STOP to opt out.', 'lead_followup_1', now() + interval '15 minutes', 'pending'),
      (v_tenant, v_from, COALESCE(p->>'to', ''), 'We do not want to miss your request. Reply with the job and ZIP when you are ready, or STOP to opt out.', 'lead_followup_2', now() + interval '24 hours', 'pending');
  END IF;

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
  IF COALESCE(p->>'message_sid', '') <> '' AND EXISTS (
    SELECT 1 FROM mctb.messages m
    WHERE m.tenant_id = v_tenant AND m.twilio_sid = p->>'message_sid'
  ) THEN
    RETURN jsonb_build_object('ok', true, 'duplicate', true);
  END IF;

  IF COALESCE(p->>'external_id', '') <> '' THEN
    INSERT INTO mctb.web_lead_events (tenant_id, external_id, payload)
    VALUES (v_tenant, p->>'external_id', COALESCE((p->>'inbound_body')::jsonb, '{}'::jsonb))
    ON CONFLICT (tenant_id, external_id) DO NOTHING;
  END IF;

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

    IF p->>'inbound_purpose' <> 'web_form' THEN
      UPDATE mctb.outbound_queue
         SET status = 'cancelled'
       WHERE tenant_id = v_tenant
         AND to_number = v_from
         AND status = 'pending'
         AND purpose LIKE 'lead_followup%';
    END IF;
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

    UPDATE mctb.conversations
       SET stage = CASE p->'estimate_update'->>'status'
         WHEN 'won' THEN 'won'
         WHEN 'lost' THEN 'lost'
         ELSE stage
       END,
       updated_at = now()
     WHERE tenant_id = v_tenant
       AND phone = (SELECT phone FROM mctb.estimates WHERE id = (p->'estimate_update'->>'id')::bigint AND tenant_id = v_tenant)
       AND p->'estimate_update'->>'status' IN ('won', 'lost');
  END IF;

  IF p->'new_appointment' IS NOT NULL AND jsonb_typeof(p->'new_appointment') = 'object' THEN
    INSERT INTO mctb.appointments (
      tenant_id, conversation_id, customer_name, phone, requested_window, status, source
    ) VALUES (
      v_tenant,
      v_conv,
      COALESCE(p->'new_appointment'->>'customer_name', ''),
      p->'new_appointment'->>'phone',
      p->'new_appointment'->>'requested_window',
      COALESCE(p->'new_appointment'->>'status', 'requested'),
      COALESCE(p->'new_appointment'->>'source', 'sms')
    );
  END IF;

  PERFORM mctb.store_outbound(v_tenant, p->'outbound', v_conv);

  IF p->>'inbound_purpose' = 'web_form' AND p->'new_appointment' IS NULL THEN
    INSERT INTO mctb.outbound_queue (tenant_id, to_number, from_number, body, purpose, send_at, status)
    VALUES (
      v_tenant, v_from, COALESCE(p->>'to', ''),
      'What day or arrival window works best for your request? Reply here and we will confirm it. Reply STOP to opt out.',
      'lead_followup_web', now() + interval '1 hour', 'pending'
    );
  END IF;

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
          'attempts', q.attempts,
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
          AND (
            q.purpose NOT LIKE 'lead_followup%'
            OR NOT EXISTS (
              SELECT 1 FROM mctb.messages m
              WHERE m.tenant_id = q.tenant_id
                AND m.direction = 'in'
                AND m.from_number = q.to_number
                AND m.created_at >= q.created_at
                AND m.purpose NOT IN ('web_form')
            )
          )
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
         SET send_at = (p->>'next_send_at')::timestamptz,
             attempts = attempts + CASE WHEN v_status = 'retry' THEN 1 ELSE 0 END,
             last_error = CASE WHEN v_status = 'retry' THEN COALESCE(p->>'error', 'provider did not return a message sid') ELSE last_error END,
             status = CASE WHEN v_status = 'retry' AND attempts >= 4 THEN 'failed' ELSE status END
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
        WHERE c.tenant_id = t.id AND c.qualification_status = 'qualified' AND c.updated_at > now() - interval '30 days'
      ),
      'booked_30d', (
        SELECT count(*) FROM mctb.appointments a
        WHERE a.tenant_id = t.id AND a.created_at > now() - interval '30 days'
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
          'phone', c.phone, 'state', c.state, 'stage', c.stage, 'source', c.source,
          'customer_name', c.customer_name, 'email', c.email, 'need', c.need, 'location', c.location,
          'urgency', c.urgency, 'qualification_status', c.qualification_status,
          'qualification_score', c.qualification_score, 'requested_window', c.requested_window,
          'updated_at', c.updated_at
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
    'appointments', COALESCE((
      SELECT jsonb_agg(x.obj) FROM (
        SELECT jsonb_build_object(
          'id', a.id, 'customer_name', a.customer_name, 'phone', a.phone,
          'requested_window', a.requested_window, 'status', a.status,
          'source', a.source, 'created_at', a.created_at
        ) AS obj
        FROM mctb.appointments a WHERE a.tenant_id = t.id ORDER BY a.id DESC LIMIT 25
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
