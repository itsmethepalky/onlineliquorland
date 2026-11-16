-- Restore inventory and related cancellation effects, then remove the order.
-- Order items and checkout deduplication rows cascade; POS sales are retained
-- and unlinked by existing ON DELETE SET NULL foreign keys. Paid Fonepay QR
-- records are retained for audit; unpaid attempts are removed.

DO $migration$
BEGIN
    IF to_regclass('public.fonepay_qr_payments') IS NULL
       OR to_regclass('public.fonepay_checkout_deduplication') IS NULL
       OR to_regprocedure('public.cancel_store_order(bigint)') IS NULL THEN
        RAISE EXCEPTION
            'Apply migrations through 030 before migration 031';
    END IF;
END;
$migration$;

ALTER TABLE public.fonepay_qr_payments
    ALTER COLUMN order_id DROP NOT NULL;

ALTER TABLE public.fonepay_qr_payments
    DROP CONSTRAINT IF EXISTS fonepay_qr_payments_order_id_fkey;

ALTER TABLE public.fonepay_qr_payments
    ADD CONSTRAINT fonepay_qr_payments_order_id_fkey
    FOREIGN KEY (order_id)
    REFERENCES public.orders(id)
    ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION public.cancel_store_order(
    p_order_id BIGINT
)
RETURNS TABLE (
    order_id BIGINT,
    order_no TEXT,
    restored BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_order RECORD;
    v_item RECORD;
    v_product_id BIGINT;
    v_pos_sale_id BIGINT;
    v_restore_credit NUMERIC;
    v_promotion_id BIGINT;
    v_restored BOOLEAN := FALSE;
BEGIN
    IF p_order_id IS NULL OR p_order_id <= 0 THEN
        RAISE EXCEPTION 'Invalid order';
    END IF;

    SELECT
        o.id,
        o.order_no,
        o.status,
        o.payment_method,
        o.customer_id,
        o.total_amount,
        o.amount_paid,
        o.promotion_id
    INTO v_order
    FROM public.orders AS o
    WHERE o.id = p_order_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Order not found';
    END IF;

    IF v_order.status <> 'cancelled'::public.order_status THEN
        IF v_order.status = 'delivered'::public.order_status THEN
            RAISE EXCEPTION 'Order cannot be cancelled';
        END IF;

        IF COALESCE(v_order.amount_paid, 0) < 0
           OR COALESCE(v_order.amount_paid, 0) > v_order.total_amount THEN
            RAISE EXCEPTION 'Order payment data is invalid';
        END IF;

        IF v_order.payment_method = 'credit'::public.payment_method THEN
            PERFORM 1
            FROM public.customers AS c
            WHERE c.id = v_order.customer_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'Customer account not found';
            END IF;

            v_restore_credit :=
                v_order.total_amount - COALESCE(v_order.amount_paid, 0);

            UPDATE public.customers
            SET credit_balance = GREATEST(0, credit_balance - v_restore_credit)
            WHERE id = v_order.customer_id;
        END IF;

        IF v_order.promotion_id IS NOT NULL THEN
            SELECT spu.promotion_id
            INTO v_promotion_id
            FROM public.store_promotion_usage AS spu
            WHERE spu.order_id = p_order_id
            FOR UPDATE;

            IF v_promotion_id IS NOT NULL THEN
                DELETE FROM public.store_promotion_usage AS spu
                WHERE spu.order_id = p_order_id;

                UPDATE public.store_promotions
                SET
                    usage_count = GREATEST(0, COALESCE(usage_count, 0) - 1),
                    updated_at = pg_catalog.now()
                WHERE id = v_promotion_id;
            END IF;
        END IF;

        FOR v_product_id IN
            SELECT DISTINCT oi.product_id
            FROM public.order_items AS oi
            WHERE oi.order_id = p_order_id
            ORDER BY oi.product_id
        LOOP
            PERFORM 1
            FROM public.products AS p
            WHERE p.id = v_product_id
            FOR UPDATE;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'Product % not found', v_product_id;
            END IF;
        END LOOP;

        FOR v_item IN
            SELECT oi.product_id, oi.quantity
            FROM public.order_items AS oi
            WHERE oi.order_id = p_order_id
            ORDER BY oi.id
        LOOP
            UPDATE public.products
            SET
                stock_quantity = stock_quantity + v_item.quantity,
                updated_at = pg_catalog.now()
            WHERE id = v_item.product_id;
        END LOOP;

        SELECT s.id
        INTO v_pos_sale_id
        FROM public.sales AS s
        WHERE s.store_order_id = p_order_id
        FOR UPDATE;

        IF v_pos_sale_id IS NOT NULL THEN
            UPDATE public.sales
            SET
                status = 'returned'::public.sale_status,
                payment_status = 'unpaid'::public.payment_status,
                amount_paid = 0
            WHERE id = v_pos_sale_id;
        END IF;

        v_restored := TRUE;
    END IF;

    DELETE FROM public.fonepay_qr_payments AS payment
    WHERE payment.order_id = p_order_id
      AND payment.status <> 'paid';

    DELETE FROM public.orders AS o
    WHERE o.id = p_order_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Order not found';
    END IF;

    RETURN QUERY
    SELECT v_order.id, v_order.order_no, v_restored;
END;
$function$;

REVOKE ALL ON FUNCTION public.cancel_store_order(BIGINT)
FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.cancel_store_order(BIGINT)
TO service_role;
