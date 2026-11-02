-- ============================================================
-- FONEPAY DYNAMIC QR PAYMENTS
--
-- Stores server-created QR payment attempts and applies verified
-- payments idempotently to existing Fonepay orders.
-- ============================================================

DO $migration$
BEGIN
    IF to_regclass('public.orders') IS NULL
       OR to_regclass('public.fonepay_order_whatsapp_notifications') IS NULL THEN
        RAISE EXCEPTION
            'Apply the order and Fonepay WhatsApp notification migrations first';
    END IF;

    IF to_regprocedure(
        'public.update_store_order_payment(bigint,public.payment_status,numeric)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'The service-role order payment RPC is missing; apply migration 007 first';
    END IF;
END;
$migration$;


CREATE TABLE IF NOT EXISTS public.fonepay_qr_payments (
    prn TEXT PRIMARY KEY,
    order_id BIGINT NOT NULL
        REFERENCES public.orders(id) ON DELETE CASCADE,
    expected_amount NUMERIC(12, 2) NOT NULL
        CHECK (expected_amount > 0),
    qr_message TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'paid', 'failed', 'expired')),
    fonepay_bid TEXT,
    fonepay_uid TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    CONSTRAINT fonepay_qr_prn_not_empty
        CHECK (LENGTH(TRIM(prn)) BETWEEN 1 AND 25),
    CONSTRAINT fonepay_qr_payment_state_check
        CHECK ((status = 'paid') = (paid_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS fonepay_qr_one_pending_per_order
    ON public.fonepay_qr_payments(order_id)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS fonepay_qr_order_idx
    ON public.fonepay_qr_payments(order_id, created_at DESC);

ALTER TABLE public.fonepay_qr_payments ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.fonepay_qr_payments
FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE
ON TABLE public.fonepay_qr_payments
TO service_role;


DROP FUNCTION IF EXISTS public.apply_fonepay_qr_payment(
    TEXT, NUMERIC, TEXT, TEXT
);

CREATE FUNCTION public.apply_fonepay_qr_payment(
    p_prn TEXT,
    p_amount NUMERIC,
    p_bid TEXT DEFAULT NULL,
    p_uid TEXT DEFAULT NULL
)
RETURNS TABLE (
    order_id BIGINT,
    order_no TEXT,
    total_amount NUMERIC,
    amount_paid NUMERIC,
    remaining_balance NUMERIC,
    payment_status TEXT,
    payment_was_already_applied BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_payment RECORD;
    v_order RECORD;
    v_new_amount NUMERIC(12, 2);
    v_payment_status public.payment_status;
BEGIN
    IF p_prn IS NULL OR LENGTH(TRIM(p_prn)) = 0 THEN
        RAISE EXCEPTION 'Fonepay PRN is required';
    END IF;

    IF p_amount IS NULL OR p_amount <= 0 THEN
        RAISE EXCEPTION 'Invalid Fonepay payment amount';
    END IF;

    SELECT *
    INTO v_payment
    FROM public.fonepay_qr_payments AS payment
    WHERE payment.prn = TRIM(p_prn)
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Fonepay QR payment was not found';
    END IF;

    IF ROUND(p_amount, 2) <> v_payment.expected_amount THEN
        RAISE EXCEPTION 'Fonepay payment amount mismatch';
    END IF;

    SELECT *
    INTO v_order
    FROM public.orders AS order_row
    WHERE order_row.id = v_payment.order_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Fonepay order was not found';
    END IF;

    IF v_payment.status = 'paid' THEN
        RETURN QUERY
        SELECT
            v_order.id,
            v_order.order_no,
            v_order.total_amount,
            v_order.amount_paid,
            GREATEST(v_order.total_amount - v_order.amount_paid, 0),
            v_order.payment_status::TEXT,
            TRUE;
        RETURN;
    END IF;

    IF v_payment.status <> 'pending' THEN
        RAISE EXCEPTION 'Fonepay QR payment is not pending';
    END IF;

    IF v_order.status = 'cancelled'::public.order_status THEN
        RAISE EXCEPTION 'Cancelled orders cannot receive payments';
    END IF;

    IF v_order.payment_method <> 'fonepay'::public.payment_method THEN
        RAISE EXCEPTION 'Order is not payable through Fonepay';
    END IF;

    v_new_amount :=
        ROUND(COALESCE(v_order.amount_paid, 0) + v_payment.expected_amount, 2);

    IF v_new_amount > v_order.total_amount THEN
        RAISE EXCEPTION 'Fonepay payment exceeds the outstanding order balance';
    END IF;

    v_payment_status :=
        CASE
            WHEN v_new_amount = v_order.total_amount
                THEN 'paid'::public.payment_status
            ELSE 'partial'::public.payment_status
        END;

    PERFORM public.update_store_order_payment(
        v_order.id,
        v_payment_status,
        v_new_amount
    );

    UPDATE public.fonepay_qr_payments
    SET
        status = 'paid',
        fonepay_bid = NULLIF(TRIM(p_bid), ''),
        fonepay_uid = NULLIF(TRIM(p_uid), ''),
        paid_at = NOW()
    WHERE prn = TRIM(p_prn);

    RETURN QUERY
    SELECT
        order_row.id,
        order_row.order_no,
        order_row.total_amount,
        order_row.amount_paid,
        GREATEST(order_row.total_amount - order_row.amount_paid, 0),
        order_row.payment_status::TEXT,
        FALSE
    FROM public.orders AS order_row
    WHERE order_row.id = v_order.id;
END;
$function$;

REVOKE ALL ON FUNCTION public.apply_fonepay_qr_payment(
    TEXT, NUMERIC, TEXT, TEXT
)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.apply_fonepay_qr_payment(
    TEXT, NUMERIC, TEXT, TEXT
)
TO service_role;


-- New Dynamic QR payment references do not live in the legacy checkout
-- intents table. Keep the notification claim idempotent for both flows.
ALTER TABLE public.fonepay_order_whatsapp_notifications
    DROP CONSTRAINT IF EXISTS
        fonepay_order_whatsapp_notifications_transaction_id_fkey;

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
    SELECT payment.order_id
    INTO v_order_id
    FROM public.fonepay_qr_payments AS payment
    WHERE payment.prn = TRIM(p_transaction_id)
      AND payment.status = 'paid';

    IF v_order_id IS NULL THEN
        SELECT intent.order_id
        INTO v_order_id
        FROM public.fonepay_checkout_intents AS intent
        WHERE intent.transaction_id = TRIM(p_transaction_id)
          AND intent.order_id IS NOT NULL
          AND intent.completed_at IS NOT NULL;
    END IF;

    IF v_order_id IS NULL THEN
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

REVOKE ALL ON FUNCTION public.claim_fonepay_whatsapp_notification(TEXT)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_fonepay_whatsapp_notification(TEXT)
TO service_role;

NOTIFY pgrst, 'reload schema';
