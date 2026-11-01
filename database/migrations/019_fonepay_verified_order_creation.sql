-- ============================================================
-- BLUE MOON STORE
-- FONEPAY CHECKOUT INTENTS + PAYMENT FINALIZATION
--
-- Creates an order only after the backend verifies successful
-- payment with Fonepay and calls finalize_fonepay_checkout().
--
-- Run this migration in the Supabase SQL Editor.
-- ============================================================

DO $migration$
BEGIN
    IF to_regclass('public.customers') IS NULL
       OR to_regclass('public.orders') IS NULL THEN
        RAISE EXCEPTION
            'Required public.customers or public.orders table is missing';
    END IF;

    IF to_regprocedure(
        'public.create_store_order_with_delivery_location(bigint,text,text,text,text,jsonb,text,numeric,double precision,double precision)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Required delivery-location order RPC is missing; apply migration 016 first';
    END IF;

    IF to_regprocedure(
        'public.update_store_order_payment(bigint,public.payment_status,numeric)'
    ) IS NULL THEN
        RAISE EXCEPTION
            'Required update_store_order_payment RPC is missing; apply migration 007 first';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_attribute
        WHERE attrelid = 'public.orders'::regclass
          AND attname = 'amount_paid'
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION
            'public.orders.amount_paid is missing; apply migration 007 first';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum AS e
        JOIN pg_type AS t ON t.oid = e.enumtypid
        JOIN pg_namespace AS n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
          AND t.typname = 'payment_method'
          AND e.enumlabel = 'fonepay'
    ) THEN
        RAISE EXCEPTION
            'The fonepay payment method is missing; apply migration 018 first';
    END IF;
END;
$migration$;


-- ============================================================
-- 1. CREATE CHECKOUT INTENTS TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS public.fonepay_checkout_intents (
    transaction_id TEXT PRIMARY KEY,
    customer_id BIGINT NOT NULL
        REFERENCES public.customers(id),

    checkout_data JSONB NOT NULL,

    amount_to_pay NUMERIC(12, 2) NOT NULL
        CHECK (amount_to_pay > 0),

    order_id BIGINT UNIQUE
        REFERENCES public.orders(id),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    CONSTRAINT fonepay_transaction_id_not_empty
        CHECK (LENGTH(TRIM(transaction_id)) > 0),

    CONSTRAINT fonepay_completion_state_check
        CHECK (
            (order_id IS NULL AND completed_at IS NULL)
            OR
            (order_id IS NOT NULL AND completed_at IS NOT NULL)
        )
);


-- ============================================================
-- 2. ADD COMPLETION CONSTRAINT IF TABLE ALREADY EXISTS
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fonepay_completion_state_check'
          AND conrelid = 'public.fonepay_checkout_intents'::regclass
    ) THEN
        ALTER TABLE public.fonepay_checkout_intents
        ADD CONSTRAINT fonepay_completion_state_check
        CHECK (
            (order_id IS NULL AND completed_at IS NULL)
            OR
            (order_id IS NOT NULL AND completed_at IS NOT NULL)
        );
    END IF;
END;
$$;


-- ============================================================
-- 3. ENABLE ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE public.fonepay_checkout_intents
ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.fonepay_checkout_intents
FROM PUBLIC, anon, authenticated;

GRANT SELECT, INSERT
ON TABLE public.fonepay_checkout_intents
TO service_role;


-- ============================================================
-- 4. FINALIZE FONEPAY CHECKOUT FUNCTION
--
-- This function:
--   1. Locks the checkout intent.
--   2. Validates the transaction and payment amount.
--   3. Prevents duplicate order creation.
--   4. Creates the store order.
--   5. Verifies the order total.
--   6. Updates the payment status.
--   7. Marks the checkout intent as completed.
--
-- IMPORTANT:
-- Call this function ONLY after the backend has verified
-- the successful Fonepay payment with the provider.
-- ============================================================

-- The return shape includes fields consumed by the backend success
-- handler. Drop the earlier version if this migration is being rerun,
-- because PostgreSQL cannot change a function's OUT parameter types
-- with CREATE OR REPLACE.
DROP FUNCTION IF EXISTS public.finalize_fonepay_checkout(TEXT, NUMERIC);

CREATE OR REPLACE FUNCTION public.finalize_fonepay_checkout(
    p_transaction_id TEXT,
    p_amount_paid NUMERIC
)
RETURNS TABLE (
    order_id BIGINT,
    order_no TEXT,
    total_amount NUMERIC,
    remaining_balance NUMERIC,
    payment_status TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_intent RECORD;
    v_checkout JSONB;
    v_order RECORD;

    v_expected_total NUMERIC(12, 2);
    v_amount_paid NUMERIC(12, 2);
    v_payment_status public.payment_status;

    v_checkout_customer_id BIGINT;
BEGIN

    -- --------------------------------------------------------
    -- Validate input
    -- --------------------------------------------------------

    IF p_transaction_id IS NULL
       OR LENGTH(TRIM(p_transaction_id)) = 0 THEN
        RAISE EXCEPTION 'Transaction ID is required';
    END IF;

    IF p_amount_paid IS NULL OR p_amount_paid <= 0 THEN
        RAISE EXCEPTION 'Invalid payment amount';
    END IF;

    v_amount_paid := ROUND(p_amount_paid, 2);


    -- --------------------------------------------------------
    -- Lock checkout intent
    -- --------------------------------------------------------

    SELECT *
    INTO v_intent
    FROM public.fonepay_checkout_intents
    WHERE transaction_id = TRIM(p_transaction_id)
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Fonepay checkout intent not found';
    END IF;


    -- --------------------------------------------------------
    -- Verify exact amount paid
    -- --------------------------------------------------------

    IF v_amount_paid <> v_intent.amount_to_pay THEN
        RAISE EXCEPTION
            'Payment amount mismatch. Expected %, received %',
            v_intent.amount_to_pay,
            v_amount_paid;
    END IF;


    -- --------------------------------------------------------
    -- Idempotency: return existing order if already finalized
    -- --------------------------------------------------------

    IF v_intent.order_id IS NOT NULL THEN

        RETURN QUERY
        SELECT
            so.id,
            so.order_no,
            so.total_amount,
            GREATEST(so.total_amount - so.amount_paid, 0),
            so.payment_status::TEXT
        FROM public.orders AS so
        WHERE so.id = v_intent.order_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Checkout is marked completed but its order does not exist';
        END IF;

        RETURN;
    END IF;


    -- --------------------------------------------------------
    -- Validate checkout data
    -- --------------------------------------------------------

    v_checkout := v_intent.checkout_data;

    IF v_checkout IS NULL
       OR jsonb_typeof(v_checkout) IS DISTINCT FROM 'object' THEN
        RAISE EXCEPTION 'Invalid checkout data';
    END IF;

    IF v_checkout = '{}'::JSONB THEN
        RAISE EXCEPTION 'Checkout data is empty';
    END IF;


    -- --------------------------------------------------------
    -- Validate customer ID
    -- --------------------------------------------------------

    IF NOT (v_checkout ? 'customer_id') THEN
        RAISE EXCEPTION 'Customer ID is missing from checkout data';
    END IF;

    IF jsonb_typeof(v_checkout->'customer_id') IS DISTINCT FROM 'number' THEN
        RAISE EXCEPTION 'Invalid customer ID in checkout data';
    END IF;

    v_checkout_customer_id :=
        (v_checkout->>'customer_id')::BIGINT;

    IF v_checkout_customer_id <> v_intent.customer_id THEN
        RAISE EXCEPTION
            'Checkout customer does not match checkout intent customer';
    END IF;


    -- --------------------------------------------------------
    -- Validate expected order total
    -- --------------------------------------------------------

    IF NOT (v_checkout ? 'expected_total_amount') THEN
        RAISE EXCEPTION 'Expected order total is missing';
    END IF;

    IF jsonb_typeof(v_checkout->'expected_total_amount')
           IS DISTINCT FROM 'number'
       AND jsonb_typeof(v_checkout->'expected_total_amount')
           IS DISTINCT FROM 'string' THEN
        RAISE EXCEPTION 'Invalid expected order total';
    END IF;

    v_expected_total :=
        ROUND(
            (v_checkout->>'expected_total_amount')::NUMERIC,
            2
        );

    IF v_expected_total <= 0 THEN
        RAISE EXCEPTION 'Expected order total must be greater than zero';
    END IF;


    -- --------------------------------------------------------
    -- Validate order items
    -- --------------------------------------------------------

    IF NOT (v_checkout ? 'items') THEN
        RAISE EXCEPTION 'Checkout items are missing';
    END IF;

    IF jsonb_typeof(v_checkout->'items') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'Checkout items must be a JSON array';
    END IF;

    IF jsonb_array_length(v_checkout->'items') = 0 THEN
        RAISE EXCEPTION 'Checkout must contain at least one item';
    END IF;


    -- --------------------------------------------------------
    -- Create store order
    --
    -- This assumes your existing function has this signature:
    --
    -- create_store_order_with_delivery_location(
    --   BIGINT, TEXT, TEXT, TEXT, TEXT, JSONB, TEXT,
    --   NUMERIC, DOUBLE PRECISION, DOUBLE PRECISION
    -- )
    --
    -- It must return order_id and total_amount.
    -- --------------------------------------------------------

    SELECT result.*
    INTO STRICT v_order
    FROM public.create_store_order_with_delivery_location(
        v_intent.customer_id,
        v_checkout->>'phone',
        v_checkout->>'shipping_address',
        'fonepay',
        v_checkout->>'notes',
        v_checkout->'items',
        v_checkout->>'promo_code',
        (v_checkout->>'delivery_distance_km')::NUMERIC,
        (v_checkout->>'delivery_latitude')::DOUBLE PRECISION,
        (v_checkout->>'delivery_longitude')::DOUBLE PRECISION
    ) AS result;


    -- --------------------------------------------------------
    -- Verify created order total
    -- --------------------------------------------------------

    IF v_order.total_amount IS NULL THEN
        RAISE EXCEPTION 'Created order has no total amount';
    END IF;

    IF ROUND(v_order.total_amount::NUMERIC, 2)
       IS DISTINCT FROM v_expected_total THEN

        RAISE EXCEPTION
            'Order total mismatch. Expected %, actual %',
            v_expected_total,
            v_order.total_amount;
    END IF;


    -- --------------------------------------------------------
    -- Ensure payment does not exceed order total
    -- --------------------------------------------------------

    IF v_amount_paid > v_order.total_amount THEN
        RAISE EXCEPTION
            'Payment amount cannot exceed order total';
    END IF;


    -- --------------------------------------------------------
    -- Determine payment status
    -- --------------------------------------------------------

    IF v_amount_paid = ROUND(v_order.total_amount::NUMERIC, 2) THEN
        v_payment_status := 'paid'::public.payment_status;
    ELSE
        v_payment_status := 'partial'::public.payment_status;
    END IF;


    -- --------------------------------------------------------
    -- Update order payment
    --
    -- This assumes your existing function accepts:
    -- update_store_order_payment(order_id, status, amount_paid)
    -- --------------------------------------------------------

    PERFORM public.update_store_order_payment(
        v_order.order_id,
        v_payment_status,
        v_amount_paid
    );


    -- --------------------------------------------------------
    -- Mark checkout intent as completed
    -- --------------------------------------------------------

    UPDATE public.fonepay_checkout_intents
    SET
        order_id = v_order.order_id,
        checkout_data = '{}'::JSONB,
        completed_at = NOW()
    WHERE transaction_id = TRIM(p_transaction_id);


    -- --------------------------------------------------------
    -- Return finalized order
    -- --------------------------------------------------------

    RETURN QUERY
    SELECT
        so.id,
        so.order_no,
        so.total_amount,
        GREATEST(so.total_amount - so.amount_paid, 0),
        so.payment_status::TEXT
    FROM public.orders AS so
    WHERE so.id = v_order.order_id;

END;
$function$;


-- ============================================================
-- 5. RESTRICT FUNCTION EXECUTION
-- ============================================================

REVOKE ALL ON FUNCTION public.finalize_fonepay_checkout(
    TEXT,
    NUMERIC
)
FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.finalize_fonepay_checkout(
    TEXT,
    NUMERIC
)
TO service_role;


-- ============================================================
-- 6. RELOAD POSTGREST SCHEMA CACHE
-- ============================================================

NOTIFY pgrst, 'reload schema';


-- ============================================================
-- 7. VERIFY MIGRATION
-- ============================================================

SELECT
    transaction_id,
    customer_id,
    amount_to_pay,
    order_id,
    created_at,
    completed_at
FROM public.fonepay_checkout_intents
ORDER BY created_at DESC
LIMIT 10;


-- Verify function exists
SELECT
    p.proname AS function_name,
    pg_get_function_identity_arguments(p.oid) AS arguments,
    pg_get_function_result(p.oid) AS return_type
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname = 'finalize_fonepay_checkout';