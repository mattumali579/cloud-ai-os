-- Subscription provider observability (docs/SUBSCRIPTION_PROVIDERS.md).
-- Tracks auth mode / quota state / task counts per provider.
-- NEVER stores tokens or credentials — auth_mode is a label, not a secret.

CREATE TABLE provider_status (
    provider             text PRIMARY KEY,
    auth_mode            text NOT NULL DEFAULT '',
    provider_type        text NOT NULL DEFAULT 'subscription_cli',
    state                text NOT NULL,
    last_success         timestamptz,
    last_auth_validation timestamptz,
    task_count           bigint NOT NULL DEFAULT 0,
    last_failure_reason  text,
    cooldown_until       timestamptz,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT provider_status_state_check CHECK (
        state IN ('AVAILABLE_SUBSCRIPTION', 'QUOTA_EXHAUSTED', 'AUTH_REQUIRED',
                  'UNAVAILABLE', 'BILLING_RISK')
    )
);
