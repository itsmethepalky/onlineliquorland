-- ============================================================
-- The Blue Moon
-- Migration 018: Fonepay-only payment cleanup
-- ============================================================
-- This migration intentionally aligns the Supabase schema with the
-- current business rules: COD and eSewa are no longer supported.
--
-- IMPORTANT:
-- PostgreSQL enum values cannot be used in the same transaction that
-- created them. For this reason, run the enum addition as a separate
-- migration/query, then run the cleanup updates in a later migration.
--
-- This avoids the error:
--   ERROR: 55P04: unsafe use of new value "fonepay" of enum type payment_method
--   HINT: New enum values must be committed before they can be used.
-- ============================================================

-- Batch 1: add the enum value in a separate execution.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON t.oid = e.enumtypid
    WHERE t.typname = 'payment_method'
      AND e.enumlabel = 'fonepay'
  ) THEN
    ALTER TYPE public.payment_method ADD VALUE 'fonepay';
  END IF;
END $$;

-- After this batch commits, run the next batch separately.

DO $$
DECLARE
  v_orders_has_payment_method boolean;
  v_orders_has_amount_paid boolean;
  v_sales_has_payment_method boolean;
  v_sales_has_amount_paid boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'orders'
      AND column_name = 'payment_method'
  ) INTO v_orders_has_payment_method;

  SELECT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'orders'
      AND column_name = 'amount_paid'
  ) INTO v_orders_has_amount_paid;

  SELECT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'sales'
      AND column_name = 'payment_method'
  ) INTO v_sales_has_payment_method;

  SELECT EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'sales'
      AND column_name = 'amount_paid'
  ) INTO v_sales_has_amount_paid;

  IF v_orders_has_payment_method THEN
    UPDATE public.orders
    SET payment_method = 'fonepay'
    WHERE payment_method::text IN ('cash', 'card', 'mobile', 'credit', 'cod');

    ALTER TABLE public.orders
      ALTER COLUMN payment_method SET DEFAULT 'fonepay';

    IF v_orders_has_amount_paid THEN
      UPDATE public.orders
      SET payment_status = 'partial'
      WHERE amount_paid > 0
        AND total_amount > 0
        AND amount_paid < total_amount
        AND payment_status IN ('paid', 'unpaid');

      UPDATE public.orders
      SET payment_status = 'paid'
      WHERE amount_paid >= total_amount
        AND total_amount > 0
        AND payment_status IN ('partial', 'unpaid');

      UPDATE public.orders
      SET payment_status = 'unpaid'
      WHERE total_amount > 0
        AND amount_paid <= 0
        AND payment_status IN ('paid', 'partial');
    END IF;

    ALTER TABLE public.orders
      DROP CONSTRAINT IF EXISTS orders_payment_method_fonepay_only;

    ALTER TABLE public.orders
      ADD CONSTRAINT orders_payment_method_fonepay_only
      CHECK (payment_method = 'fonepay') NOT VALID;
  END IF;

  IF v_sales_has_payment_method THEN
    UPDATE public.sales
    SET payment_method = 'fonepay'
    WHERE payment_method::text IN ('cash', 'card', 'mobile', 'credit', 'cod');

    ALTER TABLE public.sales
      ALTER COLUMN payment_method SET DEFAULT 'fonepay';

    IF v_sales_has_amount_paid THEN
      UPDATE public.sales
      SET payment_status = 'partial'
      WHERE amount_paid > 0
        AND total_amount > 0
        AND amount_paid < total_amount
        AND payment_status IN ('paid', 'unpaid');

      UPDATE public.sales
      SET payment_status = 'paid'
      WHERE amount_paid >= total_amount
        AND total_amount > 0
        AND payment_status IN ('partial', 'unpaid');

      UPDATE public.sales
      SET payment_status = 'unpaid'
      WHERE total_amount > 0
        AND amount_paid <= 0
        AND payment_status IN ('paid', 'partial');
    END IF;

    ALTER TABLE public.sales
      DROP CONSTRAINT IF EXISTS sales_payment_method_fonepay_only;

    ALTER TABLE public.sales
      ADD CONSTRAINT sales_payment_method_fonepay_only
      CHECK (payment_method = 'fonepay') NOT VALID;
  END IF;
END $$;

-- Optional: if your project wants the database to reject any new legacy
-- payment_method values even before the application code runs, keep the
-- constraints above enabled. If you need an even softer migration for an
-- existing live store, convert the constraints to NOT VALID only.
