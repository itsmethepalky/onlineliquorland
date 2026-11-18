DO $migration$
BEGIN
    IF to_regclass('public.fonepay_order_whatsapp_notifications') IS NULL
       OR to_regclass('public.whatsapp_webhook_events') IS NULL THEN
        RAISE EXCEPTION
            'Apply migrations 022 and 023 before WhatsApp delivery tracking';
    END IF;
END;
$migration$;

CREATE TABLE IF NOT EXISTS public.fonepay_order_whatsapp_messages (
    message_id TEXT PRIMARY KEY,
    order_id BIGINT NOT NULL
        REFERENCES public.orders(id) ON DELETE CASCADE,
    message_status TEXT,
    status_at TIMESTAMPTZ,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS fonepay_whatsapp_messages_order_idx
    ON public.fonepay_order_whatsapp_messages (order_id, created_at);

ALTER TABLE public.fonepay_order_whatsapp_messages
    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.fonepay_order_whatsapp_messages
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.fonepay_order_whatsapp_messages
    TO service_role;
GRANT SELECT ON TABLE public.whatsapp_webhook_events TO service_role;

CREATE OR REPLACE FUNCTION public.update_fonepay_whatsapp_message_status()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    IF NEW.event_type <> 'message_status' THEN
        RETURN NEW;
    END IF;

    UPDATE public.fonepay_order_whatsapp_messages AS tracked
    SET message_status = NEW.message_status,
        status_at = NEW.event_timestamp,
        error_code = NULLIF(
            NEW.payload #>> '{status,errors,0,code}',
            ''
        ),
        error_message = NULLIF(
            NEW.payload #>> '{status,errors,0,message}',
            ''
        ),
        updated_at = NOW()
    WHERE tracked.message_id = NEW.message_id
      AND (
          tracked.status_at IS NULL
          OR (
              NEW.event_timestamp IS NOT NULL
              AND NEW.event_timestamp >= tracked.status_at
          )
      );

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS whatsapp_message_status_tracking
    ON public.whatsapp_webhook_events;
CREATE TRIGGER whatsapp_message_status_tracking
AFTER INSERT ON public.whatsapp_webhook_events
FOR EACH ROW
EXECUTE FUNCTION public.update_fonepay_whatsapp_message_status();

CREATE OR REPLACE FUNCTION public.record_fonepay_whatsapp_message(
    p_transaction_id TEXT,
    p_message_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_order_id BIGINT;
    v_recorded_order_id BIGINT;
BEGIN
    IF p_message_id IS NULL OR LENGTH(TRIM(p_message_id)) = 0 THEN
        RETURN FALSE;
    END IF;

    SELECT notification.order_id
    INTO v_order_id
    FROM public.fonepay_order_whatsapp_notifications AS notification
    WHERE notification.transaction_id = TRIM(p_transaction_id)
      AND notification.claimed_at IS NOT NULL
      AND notification.sent_at IS NULL
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    INSERT INTO public.fonepay_order_whatsapp_messages (
        message_id,
        order_id
    )
    VALUES (
        TRIM(p_message_id),
        v_order_id
    )
    ON CONFLICT (message_id) DO NOTHING;

    SELECT tracked.order_id
    INTO v_recorded_order_id
    FROM public.fonepay_order_whatsapp_messages AS tracked
    WHERE tracked.message_id = TRIM(p_message_id);

    IF v_recorded_order_id IS DISTINCT FROM v_order_id THEN
        RAISE EXCEPTION
            'WhatsApp message ID is already associated with another order';
    END IF;

    UPDATE public.fonepay_order_whatsapp_messages AS tracked
    SET message_status = latest_status.message_status,
        status_at = latest_status.event_timestamp,
        error_code = NULLIF(
            latest_status.payload #>> '{status,errors,0,code}',
            ''
        ),
        error_message = NULLIF(
            latest_status.payload #>> '{status,errors,0,message}',
            ''
        ),
        updated_at = NOW()
    FROM (
        SELECT webhook.message_status,
               webhook.event_timestamp,
               webhook.payload
        FROM public.whatsapp_webhook_events AS webhook
        WHERE webhook.message_id = TRIM(p_message_id)
          AND webhook.event_type = 'message_status'
        ORDER BY webhook.event_timestamp DESC NULLS LAST,
                 webhook.received_at DESC
        LIMIT 1
    ) AS latest_status
    WHERE tracked.message_id = TRIM(p_message_id)
      AND (
          tracked.status_at IS NULL
          OR (
              latest_status.event_timestamp IS NOT NULL
              AND latest_status.event_timestamp >= tracked.status_at
          )
      );

    RETURN TRUE;
END;
$function$;

REVOKE ALL ON FUNCTION public.record_fonepay_whatsapp_message(TEXT, TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_fonepay_whatsapp_message(TEXT, TEXT)
    TO service_role;

NOTIFY pgrst, 'reload schema';
