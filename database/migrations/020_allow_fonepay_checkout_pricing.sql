-- ============================================================
-- BLUE MOON STORE
-- MIGRATION 020: ALLOW FONEPAY IN ROUTE-PRICED CHECKOUT RPCS
--
-- Migration 015 clones the checkout and preview functions. Those
-- copies need to accept the Fonepay payment method added in 018.
-- This updates existing databases without replacing other function
-- logic or changing their signatures.
-- ============================================================

DO $migration$
DECLARE
    v_signature TEXT;
    v_function_oid OID;
    v_definition TEXT;
    v_updated_definition TEXT;
BEGIN
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

    FOREACH v_signature IN ARRAY ARRAY[
        'public.create_store_order_with_delivery(bigint,text,text,text,text,jsonb,text,numeric)',
        'public.preview_store_checkout_with_delivery(bigint,text,text,text,jsonb,text,numeric)'
    ]
    LOOP
        v_function_oid := to_regprocedure(v_signature);
        IF v_function_oid IS NULL THEN
            RAISE EXCEPTION
                'Required route-priced checkout function is missing: %',
                v_signature;
        END IF;

        v_definition := pg_get_functiondef(v_function_oid);
        v_updated_definition := regexp_replace(
            v_definition,
            '(p_payment_method[[:space:]]+not[[:space:]]+in[[:space:]]*[(][[:space:]]*''cash''[[:space:]]*,[[:space:]]*''card''[[:space:]]*,[[:space:]]*''mobile''[[:space:]]*,[[:space:]]*''credit''[[:space:]]*,[[:space:]]*''cod''[[:space:]]*)[)]',
            E'\\1, ''fonepay'')',
            'i'
        );

        IF v_updated_definition = v_definition
           AND v_definition !~*
               'p_payment_method[[:space:]]+not[[:space:]]+in[[:space:]]*[(][^)]*''fonepay''[^)]*[)]' THEN
            RAISE EXCEPTION
                'Could not verify or update the Fonepay payment-method check in %',
                v_signature;
        END IF;

        IF v_updated_definition <> v_definition THEN
            EXECUTE v_updated_definition;
        END IF;
    END LOOP;
END;
$migration$;

NOTIFY pgrst, 'reload schema';
