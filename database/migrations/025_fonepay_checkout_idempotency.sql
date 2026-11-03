DO $migration$
BEGIN
    IF to_regclass('public.orders') IS NULL
       OR to_regclass('public.customers') IS NULL
       OR to_regclass('public.fonepay_qr_payments') IS NULL
       OR to_regprocedure(
            'public.create_store_order_with_delivery_location(bigint,text,text,text,text,jsonb,text,numeric,double precision,double precision)'
       ) IS NULL THEN
        RAISE EXCEPTION
            'Apply checkout migrations through 024 before migration 025';
    END IF;
END;
$migration$;


CREATE TABLE IF NOT EXISTS public.fonepay_checkout_deduplication (
    customer_id BIGINT NOT NULL
        REFERENCES public.customers(id) ON DELETE CASCADE,
    cart_fingerprint TEXT NOT NULL
        CHECK (cart_fingerprint ~ '^[0-9a-f]{64}$'),
    order_id BIGINT NOT NULL
        REFERENCES public.orders(id) ON DELETE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (customer_id, cart_fingerprint),
    UNIQUE (order_id)
);

ALTER TABLE public.fonepay_checkout_deduplication
    ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.fonepay_checkout_deduplication
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE
    ON TABLE public.fonepay_checkout_deduplication
    TO service_role;

CREATE OR REPLACE FUNCTION public.create_store_order_with_delivery_location_idempotent(
    p_customer_id BIGINT,
    p_phone TEXT,
    p_shipping_address TEXT,
    p_payment_method TEXT,
    p_notes TEXT,
    p_items JSONB,
    p_promo_code TEXT,
    p_delivery_distance_km NUMERIC,
    p_delivery_latitude DOUBLE PRECISION,
    p_delivery_longitude DOUBLE PRECISION,
    p_cart_fingerprint TEXT
)
RETURNS TABLE (
    order_id BIGINT,
    order_no TEXT,
    subtotal NUMERIC,
    discount NUMERIC,
    delivery_charge NUMERIC,
    promo_code TEXT,
    total_amount NUMERIC
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_existing_order_id BIGINT;
    v_order_no TEXT;
    v_subtotal NUMERIC;
    v_discount NUMERIC;
    v_delivery_charge NUMERIC;
    v_promo_code TEXT;
    v_total_amount NUMERIC;
    v_order_status TEXT;
    v_payment_status TEXT;
BEGIN
    IF p_cart_fingerprint IS NULL
       OR p_cart_fingerprint !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid checkout cart fingerprint';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            p_customer_id::TEXT || ':' || p_cart_fingerprint,
            0
        )
    );

    SELECT deduplication.order_id
    INTO v_existing_order_id
    FROM public.fonepay_checkout_deduplication AS deduplication
    WHERE deduplication.customer_id = p_customer_id
      AND deduplication.cart_fingerprint = p_cart_fingerprint
      AND deduplication.is_active
    FOR UPDATE;

    IF FOUND THEN
        SELECT
            order_row.order_no,
            order_row.subtotal,
            order_row.discount,
            order_row.delivery_charge,
            order_row.promo_code,
            order_row.total_amount,
            order_row.status::TEXT,
            order_row.payment_status::TEXT
        INTO
            v_order_no,
            v_subtotal,
            v_discount,
            v_delivery_charge,
            v_promo_code,
            v_total_amount,
            v_order_status,
            v_payment_status
        FROM public.orders AS order_row
        WHERE order_row.id = v_existing_order_id
        FOR UPDATE;

        IF FOUND
           AND v_order_status <> 'cancelled'
           AND v_payment_status <> 'paid' THEN
            RETURN QUERY
            SELECT
                v_existing_order_id,
                v_order_no,
                v_subtotal,
                v_discount,
                v_delivery_charge,
                v_promo_code,
                v_total_amount;
            RETURN;
        END IF;

        UPDATE public.fonepay_checkout_deduplication
        SET is_active = FALSE
        WHERE customer_id = p_customer_id
          AND cart_fingerprint = p_cart_fingerprint;
    END IF;

    v_existing_order_id := NULL;
    SELECT
        created.order_id,
        created.order_no,
        created.subtotal,
        created.discount,
        created.delivery_charge,
        created.promo_code,
        created.total_amount
    INTO
        v_existing_order_id,
        v_order_no,
        v_subtotal,
        v_discount,
        v_delivery_charge,
        v_promo_code,
        v_total_amount
    FROM public.create_store_order_with_delivery_location(
        p_customer_id,
        p_phone,
        p_shipping_address,
        p_payment_method,
        p_notes,
        p_items,
        p_promo_code,
        p_delivery_distance_km,
        p_delivery_latitude,
        p_delivery_longitude
    ) AS created;

    IF v_existing_order_id IS NULL THEN
        RAISE EXCEPTION 'Checkout did not create an order';
    END IF;

    INSERT INTO public.fonepay_checkout_deduplication AS deduplication (
        customer_id,
        cart_fingerprint,
        order_id,
        is_active
    )
    VALUES (
        p_customer_id,
        p_cart_fingerprint,
        v_existing_order_id,
        TRUE
    )
    ON CONFLICT (customer_id, cart_fingerprint)
    DO UPDATE SET
        order_id = EXCLUDED.order_id,
        is_active = TRUE,
        created_at = NOW();

    RETURN QUERY
    SELECT
        v_existing_order_id,
        v_order_no,
        v_subtotal,
        v_discount,
        v_delivery_charge,
        v_promo_code,
        v_total_amount;
END;
$function$;

REVOKE ALL ON FUNCTION public.create_store_order_with_delivery_location_idempotent(
    BIGINT,
    TEXT,
    TEXT,
    TEXT,
    TEXT,
    JSONB,
    TEXT,
    NUMERIC,
    DOUBLE PRECISION,
    DOUBLE PRECISION,
    TEXT
)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_store_order_with_delivery_location_idempotent(
    BIGINT,
    TEXT,
    TEXT,
    TEXT,
    TEXT,
    JSONB,
    TEXT,
    NUMERIC,
    DOUBLE PRECISION,
    DOUBLE PRECISION,
    TEXT
)
TO service_role;

NOTIFY pgrst, 'reload schema';
