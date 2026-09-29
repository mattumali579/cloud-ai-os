-- 007_conversation_memory.sql — the REPLY -> MEMORY -> NOTIFICATION -> SALES layer
-- on top of the lead engine (006). Everything keys off companies.company_id.
--
-- Rules the schema itself enforces (not left to application code):
--   * outreach_messages is append-only: no UPDATE of content, no DELETE.
--   * one authoritative status per company (company_conversation_state).
--   * a company in any post-reply / closed status can never have an active
--     cold sequence ("won + cold_followup_pending" is impossible).
--   * do_not_contact = true forces status do_not_contact and a stopped sequence.
--   * email_suppressions is checked by the send guard before ANY send.
--   * notifications get a dedupe_key so each event pings the owner once.

-- ---------------------------------------------------------------- messages
CREATE TABLE IF NOT EXISTS outreach_messages (
    message_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id           uuid REFERENCES companies (company_id) ON DELETE RESTRICT,
    direction            text NOT NULL CHECK (direction IN ('outbound','inbound')),
    kind                 text NOT NULL DEFAULT 'other'
                         CHECK (kind IN ('cold','followup','reply','pricing','audit','proposal',
                                         'manual','auto_reply','bounce','inbound','other')),
    sender               text NOT NULL DEFAULT '',
    recipient            text NOT NULL DEFAULT '',
    subject              text NOT NULL DEFAULT '',
    body                 text NOT NULL DEFAULT '',
    occurred_at          timestamptz NOT NULL,
    provider             text NOT NULL DEFAULT '',
    provider_message_id  text,              -- RFC 5322 Message-ID (angle brackets stripped, lower-cased)
    thread_id            text,              -- provider thread id (Gmail X-GM-THRID) when known
    in_reply_to          text,
    reference_ids        text[] NOT NULL DEFAULT '{}',
    offer                text,
    copy_variant         text,
    cta                  text,              -- the question our email asked, if any
    price_quoted         text,              -- exact price text we put in this email, if any
    match_method         text,              -- inbound only: how the company was identified
    match_confidence     text CHECK (match_confidence IN ('high','medium','low','none')),
    source_ref           text,              -- e.g. airtable:recXXX, imap:INBOX:1234
    meta                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at          timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_outreach_messages_provider_id
    ON outreach_messages (provider_message_id) WHERE provider_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outreach_messages_company ON outreach_messages (company_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_outreach_messages_thread ON outreach_messages (thread_id) WHERE thread_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outreach_messages_recipient ON outreach_messages (lower(recipient)) WHERE direction = 'outbound';

-- History is permanent. Only company_id may be filled in later (an unmatched
-- inbound message resolved by the owner); nothing else about a message changes.
CREATE OR REPLACE FUNCTION outreach_messages_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'outreach_messages is append-only (delete refused)';
    END IF;
    IF (OLD.company_id IS NOT NULL AND NEW.company_id IS DISTINCT FROM OLD.company_id)
       OR NEW.direction IS DISTINCT FROM OLD.direction
       OR NEW.sender IS DISTINCT FROM OLD.sender
       OR NEW.recipient IS DISTINCT FROM OLD.recipient
       OR NEW.subject IS DISTINCT FROM OLD.subject
       OR NEW.body IS DISTINCT FROM OLD.body
       OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at
       OR NEW.provider_message_id IS DISTINCT FROM OLD.provider_message_id THEN
        RAISE EXCEPTION 'outreach_messages is append-only (content change refused)';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_outreach_messages_immutable ON outreach_messages;
CREATE TRIGGER trg_outreach_messages_immutable BEFORE UPDATE OR DELETE ON outreach_messages
    FOR EACH ROW EXECUTE FUNCTION outreach_messages_immutable();

-- One row per classification attempt (re-classifying never erases the old one).
CREATE TABLE IF NOT EXISTS reply_analyses (
    analysis_id          bigserial PRIMARY KEY,
    message_id           uuid NOT NULL REFERENCES outreach_messages (message_id),
    company_id           uuid REFERENCES companies (company_id),
    classification       text NOT NULL CHECK (classification IN (
                           'INTERESTED','PRICE_QUESTION','MORE_INFORMATION','MEETING_REQUEST','READY_TO_BUY',
                           'OBJECTION','NOT_NOW','REFERRAL','WRONG_PERSON','NOT_INTERESTED','UNSUBSCRIBE',
                           'AUTO_REPLY','DELIVERY_FAILURE','OTHER','NEEDS_REVIEW')),
    confidence           numeric(4,3) NOT NULL,
    evidence             jsonb NOT NULL DEFAULT '[]'::jsonb,
    conversation_summary text NOT NULL DEFAULT '',
    interpretation       text NOT NULL DEFAULT '',
    recommended_action   text NOT NULL DEFAULT '',
    needs_review_reason  text,
    classifier_version   text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reply_analyses_message ON reply_analyses (message_id);
CREATE INDEX IF NOT EXISTS idx_reply_analyses_company ON reply_analyses (company_id, created_at);

-- ------------------------------------------------------------------- state
CREATE TABLE IF NOT EXISTS company_conversation_state (
    company_id               uuid PRIMARY KEY REFERENCES companies (company_id) ON DELETE CASCADE,
    company_name             text NOT NULL DEFAULT '',
    contact_email            text,
    current_status           text NOT NULL DEFAULT 'discovered' CHECK (current_status IN (
                               'discovered','ready','emailed','replied','interested','qualified',
                               'meeting_requested','proposal_needed','proposal_sent','negotiating',
                               'won','lost','not_now','do_not_contact','needs_review')),
    previous_status          text,
    last_reply_classification text,
    last_reply_at            timestamptz,
    last_meaningful_message_id uuid REFERENCES outreach_messages (message_id),
    last_meaningful_at       timestamptz,
    last_meaningful_summary  text,
    current_offer            text,
    current_price            text,
    outstanding_question     text,
    next_action              text,
    next_action_due          timestamptz,
    follow_up_on             date,
    proposal_status          text NOT NULL DEFAULT 'none'
                             CHECK (proposal_status IN ('none','needed','drafted','sent','accepted','declined')),
    cold_sequence_active     boolean NOT NULL DEFAULT false,
    sequence_stop_reason     text,
    do_not_contact           boolean NOT NULL DEFAULT false,
    bounced                  boolean NOT NULL DEFAULT false,
    high_value               boolean NOT NULL DEFAULT false,
    airtable_record_id       text,
    version                  int NOT NULL DEFAULT 0,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    -- no cold/follow-up sequence once a company has replied, closed, or needs review
    CONSTRAINT cold_sequence_only_before_reply CHECK (
        NOT cold_sequence_active OR current_status IN ('discovered','ready','emailed')),
    CONSTRAINT dnc_is_terminal CHECK (
        NOT do_not_contact OR (current_status = 'do_not_contact' AND NOT cold_sequence_active)),
    CONSTRAINT bounced_stops_sequence CHECK (NOT bounced OR NOT cold_sequence_active)
);
CREATE INDEX IF NOT EXISTS idx_conv_state_status ON company_conversation_state (current_status, updated_at);

CREATE TABLE IF NOT EXISTS company_status_transitions (
    id          bigserial PRIMARY KEY,
    company_id  uuid NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    from_status text,
    to_status   text NOT NULL,
    reason      text NOT NULL,
    message_id  uuid REFERENCES outreach_messages (message_id),
    actor       text NOT NULL DEFAULT 'system',
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_status_transitions_company ON company_status_transitions (company_id, at);

-- Structured things a prospect told us, so they never have to repeat themselves.
CREATE TABLE IF NOT EXISTS sales_facts (
    fact_id            bigserial PRIMARY KEY,
    company_id         uuid NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    fact_type          text NOT NULL CHECK (fact_type IN (
                         'offer_discussed','price_discussed','price_objection','objection','question',
                         'commitment','timing','decision_maker','preference','service_interest',
                         'scope_exclusion','referral','meeting_request','other')),
    fact_text          text NOT NULL,
    value              jsonb NOT NULL DEFAULT '{}'::jsonb,
    said_by            text NOT NULL CHECK (said_by IN ('prospect','us','owner')),
    source_message_id  uuid REFERENCES outreach_messages (message_id),
    active             boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_facts_dedupe
    ON sales_facts (company_id, fact_type, lower(fact_text), coalesce(source_message_id, '00000000-0000-0000-0000-000000000000'::uuid));

CREATE TABLE IF NOT EXISTS email_suppressions (
    id                 bigserial PRIMARY KEY,
    email              text,
    domain             text,
    company_id         uuid REFERENCES companies (company_id) ON DELETE SET NULL,
    reason             text NOT NULL,     -- unsubscribe | do_not_contact | hard_bounce | legal | manual
    source_message_id  uuid REFERENCES outreach_messages (message_id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (email IS NOT NULL OR domain IS NOT NULL OR company_id IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_suppression_email ON email_suppressions (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_suppression_company ON email_suppressions (company_id) WHERE email IS NULL AND domain IS NULL;

-- The ONE list of things that need Matt. Everything else is handled or quiet.
CREATE TABLE IF NOT EXISTS human_attention_queue (
    item_id              bigserial PRIMARY KEY,
    company_id           uuid REFERENCES companies (company_id) ON DELETE CASCADE,
    message_id           uuid REFERENCES outreach_messages (message_id),
    reason_code          text NOT NULL,
    reason_human_needed  text NOT NULL,
    urgency              text NOT NULL CHECK (urgency IN ('urgent','high','normal')),
    status_snapshot      text,
    last_reply           text,
    summary              text NOT NULL DEFAULT '',
    recommended_action   text NOT NULL DEFAULT '',
    record_link          text,
    state                text NOT NULL DEFAULT 'open' CHECK (state IN ('open','resolved')),
    resolution           text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    resolved_at          timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_attention_dedupe
    ON human_attention_queue (coalesce(company_id, '00000000-0000-0000-0000-000000000000'::uuid),
                              coalesce(message_id, '00000000-0000-0000-0000-000000000000'::uuid), reason_code);
CREATE INDEX IF NOT EXISTS idx_attention_open ON human_attention_queue (urgency, created_at) WHERE state = 'open';

-- Prepared replies / audits / proposals. NEVER sent by this layer: a draft is
-- only ever sent by the sender after approval, and then it becomes a message.
CREATE TABLE IF NOT EXISTS outreach_drafts (
    draft_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id            uuid NOT NULL REFERENCES companies (company_id) ON DELETE CASCADE,
    kind                  text NOT NULL CHECK (kind IN ('reply','pricing','audit','proposal','referral_intro','objection')),
    based_on_message_id   uuid REFERENCES outreach_messages (message_id),
    to_email              text,
    subject               text NOT NULL DEFAULT '',
    body                  text NOT NULL,
    content               jsonb NOT NULL DEFAULT '{}'::jsonb,
    state                 text NOT NULL DEFAULT 'awaiting_approval'
                          CHECK (state IN ('awaiting_approval','approved','sent','discarded','superseded')),
    sent_message_id       uuid REFERENCES outreach_messages (message_id),
    created_at            timestamptz NOT NULL DEFAULT now(),
    decided_at            timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_drafts_per_message
    ON outreach_drafts (company_id, kind, coalesce(based_on_message_id, '00000000-0000-0000-0000-000000000000'::uuid))
    WHERE state IN ('awaiting_approval','approved');

-- Revenue closes the loop for the lead-generator feedback report.
CREATE TABLE IF NOT EXISTS deals (
    company_id   uuid PRIMARY KEY REFERENCES companies (company_id) ON DELETE CASCADE,
    offer        text,
    setup_usd    numeric(10,2),
    monthly_usd  numeric(10,2),
    won_at       timestamptz,
    lost_at      timestamptz,
    note         text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Exactly-once owner notifications: the same event key is inserted once, ever.
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS dedupe_key text;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS company_id uuid;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivered_at timestamptz;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS delivery_receipt text;
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS attempts int NOT NULL DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_dedupe ON notifications (dedupe_key) WHERE dedupe_key IS NOT NULL;

-- Inbound mail we could not tie to a company still gets stored (company_id NULL)
-- and lands in the attention queue; the poller remembers where it left off.
CREATE TABLE IF NOT EXISTS reply_poll_state (
    mailbox      text PRIMARY KEY,
    last_uid     bigint NOT NULL DEFAULT 0,
    uidvalidity  bigint,
    last_run_at  timestamptz,
    last_result  jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ------------------------------------------------------------ send guard
-- The ONE question every sender asks immediately before any send:
--   SELECT outreach_send_check('john@abc.com', 'followup');
-- Returns {"allowed": bool, "reasons": [...], "company_id": ..., "status": ...}.
-- kind: cold | followup (generic sequence) | reply | pricing | audit | proposal (owner-approved replies)
CREATE OR REPLACE FUNCTION outreach_send_check(p_email text, p_kind text, p_company_id uuid DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_email   text := lower(trim(coalesce(p_email, '')));
    v_domain  text := split_part(lower(trim(coalesce(p_email, ''))), '@', 2);
    v_company uuid := p_company_id;
    v_state   company_conversation_state%ROWTYPE;
    v_status  text;
    v_reasons text[] := '{}';
    v_generic boolean := p_kind IN ('cold', 'followup');
BEGIN
    IF p_kind NOT IN ('cold','followup','reply','pricing','audit','proposal','referral_intro','objection') THEN
        RETURN jsonb_build_object('allowed', false, 'reasons', jsonb_build_array('unknown_kind'));
    END IF;
    IF v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' THEN
        RETURN jsonb_build_object('allowed', false, 'reasons', jsonb_build_array('invalid_email'));
    END IF;
    IF v_company IS NULL THEN
        SELECT company_id INTO v_company FROM contacts WHERE lower(email) = v_email LIMIT 1;
    END IF;
    IF v_company IS NULL THEN
        SELECT company_id INTO v_company FROM outreach_messages
         WHERE company_id IS NOT NULL AND (lower(recipient) = v_email OR lower(sender) = v_email)
         ORDER BY occurred_at DESC LIMIT 1;
    END IF;
    IF v_company IS NULL AND v_domain <> '' THEN
        -- another address at a known company's own website domain (lead engine never stores freemail here)
        SELECT company_id INTO v_company FROM companies WHERE normalized_domain = v_domain LIMIT 1;
    END IF;

    IF EXISTS (SELECT 1 FROM email_suppressions WHERE lower(email) = v_email) THEN
        v_reasons := array_append(v_reasons, 'email_suppressed'::text); END IF;
    IF v_domain <> '' AND EXISTS (SELECT 1 FROM email_suppressions WHERE domain = v_domain) THEN
        v_reasons := array_append(v_reasons, 'domain_suppressed'::text); END IF;

    IF v_company IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM email_suppressions WHERE company_id = v_company) THEN
            v_reasons := array_append(v_reasons, 'company_suppressed'::text); END IF;
        SELECT * INTO v_state FROM company_conversation_state WHERE company_id = v_company;
        IF FOUND THEN
            v_status := v_state.current_status;
            IF v_state.do_not_contact OR v_status = 'do_not_contact' THEN v_reasons := array_append(v_reasons, 'do_not_contact'::text); END IF;
            IF v_status = 'needs_review' THEN v_reasons := array_append(v_reasons, 'needs_review_pending'::text); END IF;
            IF v_state.bounced AND v_generic THEN v_reasons := array_append(v_reasons, 'bounced'::text); END IF;
            IF v_generic AND v_status NOT IN ('discovered','ready','emailed') THEN
                v_reasons := array_append(v_reasons, ('status_' || v_status || '_stops_generic_sequence')::text); END IF;
            IF v_generic AND v_status = 'emailed' AND NOT v_state.cold_sequence_active AND p_kind = 'followup' THEN
                v_reasons := array_append(v_reasons, 'sequence_stopped'::text); END IF;
        ELSE
            SELECT CASE outreach_status WHEN 'unsubscribed' THEN 'do_not_contact' WHEN 'do_not_contact' THEN 'do_not_contact'
                   ELSE NULL END INTO v_status FROM companies WHERE company_id = v_company;
            IF v_status = 'do_not_contact' THEN v_reasons := array_append(v_reasons, 'do_not_contact'::text); END IF;
        END IF;
        IF v_generic AND EXISTS (SELECT 1 FROM outreach_messages WHERE company_id = v_company AND direction = 'inbound'
                                 AND kind NOT IN ('auto_reply','bounce')) THEN
            v_reasons := array_append(v_reasons, 'reply_received'::text); END IF;
        IF EXISTS (SELECT 1 FROM human_attention_queue WHERE company_id = v_company AND state = 'open'
                   AND reason_code IN ('legal_or_angry','conflict_unsubscribe_and_positive')) THEN
            v_reasons := array_append(v_reasons, 'owner_must_handle'::text); END IF;
    END IF;

    -- duplicate first touch: any confirmed send to this company or address already
    IF p_kind = 'cold' AND (
         EXISTS (SELECT 1 FROM outreach_messages WHERE direction = 'outbound'
                 AND ((v_company IS NOT NULL AND company_id = v_company) OR lower(recipient) = v_email))
      OR EXISTS (SELECT 1 FROM outreach_history WHERE status = 'sent'
                 AND ((v_company IS NOT NULL AND company_id = v_company) OR lower(email) = v_email))
      OR EXISTS (SELECT 1 FROM companies WHERE company_id = v_company AND first_contacted_at IS NOT NULL)) THEN
        v_reasons := array_append(v_reasons, 'already_contacted'::text);
    END IF;
    IF p_kind = 'followup' AND v_company IS NULL
       AND NOT EXISTS (SELECT 1 FROM outreach_history WHERE lower(email) = v_email AND status = 'sent') THEN
        v_reasons := array_append(v_reasons, 'no_first_touch_on_record'::text);
    END IF;

    RETURN jsonb_build_object('allowed', cardinality(v_reasons) = 0, 'reasons', to_jsonb(v_reasons),
                              'company_id', v_company, 'status', v_status, 'kind', p_kind, 'checked_at', now());
END $$;

ALTER TABLE outreach_messages           ENABLE ROW LEVEL SECURITY;
ALTER TABLE reply_analyses              ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_conversation_state  ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_status_transitions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE sales_facts                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_suppressions          ENABLE ROW LEVEL SECURITY;
ALTER TABLE human_attention_queue       ENABLE ROW LEVEL SECURITY;
ALTER TABLE outreach_drafts             ENABLE ROW LEVEL SECURITY;
ALTER TABLE deals                       ENABLE ROW LEVEL SECURITY;
ALTER TABLE reply_poll_state            ENABLE ROW LEVEL SECURITY;
