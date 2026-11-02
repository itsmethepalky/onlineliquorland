-- ============================================================
-- BLUE MOON STORE
-- MIGRATION 022: IDEMPOTENT FONEPAY ORDER NOTIFICATION CLAIMS
--
-- Tracks WhatsApp notification delivery without allowing duplicate
-- payment callbacks to send duplicate admin notifications.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.fonepay_order_whatsapp_notifications (
    order_id BIGINT PRIMARY KEY
        REFERENCES public.orders(id) ON DELETE CASCADE,
    transaction_id TEXT NOT NULL UNIQUE
        REFERENCES public.fonepay_checkout_intents(transaction_id)
        ON DELETE CASCADE,
    claimed_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fonepay_whatsapp_sent_claim_check
        CHECK (sent_at IS NULL OR claimed_at IS NULL)
);

ALTER TABLE public.fonepay_order_whatsapp_notifications
ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.fonepay_order_whatsapp_notifications
FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT, UPDATE
ON TABLE public.fonepay_order_whatsapp_notifications
TO service_role;

CREATE OR REPLACE FUNCTION public.claim_fonepay_whatsapp_notification(
    p_transaction_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_order_id BIGINT;
    v_claimed_order_id BIGINT;
BEGIN
    SELECT intent.order_id
    INTO v_order_id
    FROM public.fonepay_checkout_intents AS intent
    WHERE intent.transaction_id = TRIM(p_transaction_id)
      AND intent.order_id IS NOT NULL
      AND intent.completed_at IS NOT NULL;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    INSERT INTO public.fonepay_order_whatsapp_notifications AS notification (
        order_id,
        transaction_id,
        claimed_at
    )
    VALUES (
        v_order_id,
        TRIM(p_transaction_id),
        NOW()
    )
    ON CONFLICT (order_id) DO UPDATE
    SET claimed_at = NOW()
    WHERE notification.sent_at IS NULL
      AND (
          notification.claimed_at IS NULL
          OR notification.claimed_at < NOW() - INTERVAL '5 minutes'
      )
    RETURNING order_id INTO v_claimed_order_id;

    RETURN v_claimed_order_id IS NOT NULL;
END;
$function$;

CREATE OR REPLACE FUNCTION public.mark_fonepay_whatsapp_notification_sent(
    p_transaction_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    UPDATE public.fonepay_order_whatsapp_notifications
    SET sent_at = NOW(),
        claimed_at = NULL
    WHERE transaction_id = TRIM(p_transaction_id)
      AND claimed_at IS NOT NULL
      AND sent_at IS NULL;

    RETURN FOUND;
END;
$function$;

CREATE OR REPLACE FUNCTION public.release_fonepay_whatsapp_notification(
    p_transaction_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    UPDATE public.fonepay_order_whatsapp_notifications
    SET claimed_at = NULL
    WHERE transaction_id = TRIM(p_transaction_id)
      AND claimed_at IS NOT NULL
      AND sent_at IS NULL;

    RETURN FOUND;
END;
$function$;

REVOKE ALL ON FUNCTION public.claim_fonepay_whatsapp_notification(TEXT)
FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.mark_fonepay_whatsapp_notification_sent(TEXT)
FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.release_fonepay_whatsapp_notification(TEXT)
FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.claim_fonepay_whatsapp_notification(TEXT)
TO service_role;
GRANT EXECUTE ON FUNCTION public.mark_fonepay_whatsapp_notification_sent(TEXT)
TO service_role;
GRANT EXECUTE ON FUNCTION public.release_fonepay_whatsapp_notification(TEXT)
TO service_role;

NOTIFY pgrst, 'reload schema';
