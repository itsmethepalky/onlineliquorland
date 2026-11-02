-- Store normalized WhatsApp messages and message-status webhook events.
-- The table is private to the backend service role.

CREATE TABLE IF NOT EXISTS public.whatsapp_webhook_events (
    event_key TEXT PRIMARY KEY,
    event_type TEXT NOT NULL
        CHECK (event_type IN ('incoming_message', 'message_status')),
    business_account_id TEXT,
    phone_number_id TEXT,
    message_id TEXT NOT NULL,
    message_status TEXT,
    event_timestamp TIMESTAMPTZ,
    sender_wa_id TEXT,
    recipient_wa_id TEXT,
    message_type TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_webhook_event_type_shape_check
        CHECK (
            (event_type = 'incoming_message' AND message_status IS NULL)
            OR
            (event_type = 'message_status' AND message_status IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS whatsapp_webhook_events_message_idx
    ON public.whatsapp_webhook_events (message_id, event_timestamp);

CREATE INDEX IF NOT EXISTS whatsapp_webhook_events_received_idx
    ON public.whatsapp_webhook_events (received_at);

ALTER TABLE public.whatsapp_webhook_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.whatsapp_webhook_events
    FROM PUBLIC, anon, authenticated;

GRANT INSERT ON TABLE public.whatsapp_webhook_events TO service_role;
