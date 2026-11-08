CREATE TABLE IF NOT EXISTS public.product_stock_updates (
    product_id BIGINT PRIMARY KEY
        REFERENCES public.products(id) ON DELETE CASCADE,
    stock_quantity INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE public.product_stock_updates ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.product_stock_updates
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON TABLE public.product_stock_updates
    TO anon, authenticated;

DROP POLICY IF EXISTS product_stock_updates_public_read
    ON public.product_stock_updates;
CREATE POLICY product_stock_updates_public_read
    ON public.product_stock_updates
    FOR SELECT
    TO anon, authenticated
    USING (TRUE);

CREATE OR REPLACE FUNCTION public.sync_product_stock_update()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    IF TG_OP = 'UPDATE'
       AND NEW.stock_quantity IS NOT DISTINCT FROM OLD.stock_quantity
       AND NEW.is_active IS NOT DISTINCT FROM OLD.is_active THEN
        RETURN NEW;
    END IF;

    INSERT INTO public.product_stock_updates (
        product_id,
        stock_quantity,
        is_active,
        updated_at
    )
    VALUES (
        NEW.id,
        NEW.stock_quantity,
        NEW.is_active,
        NEW.updated_at
    )
    ON CONFLICT (product_id)
    DO UPDATE SET
        stock_quantity = EXCLUDED.stock_quantity,
        is_active = EXCLUDED.is_active,
        updated_at = EXCLUDED.updated_at;

    RETURN NEW;
END;
$function$;

REVOKE ALL ON FUNCTION public.sync_product_stock_update()
    FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS products_sync_stock_update
    ON public.products;
CREATE TRIGGER products_sync_stock_update
    AFTER INSERT OR UPDATE ON public.products
    FOR EACH ROW
    EXECUTE FUNCTION public.sync_product_stock_update();

INSERT INTO public.product_stock_updates (
    product_id,
    stock_quantity,
    is_active,
    updated_at
)
SELECT id, stock_quantity, is_active, updated_at
FROM public.products
ON CONFLICT (product_id)
DO UPDATE SET
    stock_quantity = EXCLUDED.stock_quantity,
    is_active = EXCLUDED.is_active,
    updated_at = EXCLUDED.updated_at;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_publication
        WHERE pubname = 'supabase_realtime'
    ) THEN
        RAISE EXCEPTION
            'Supabase Realtime publication is unavailable';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'product_stock_updates'
    ) THEN
        ALTER PUBLICATION supabase_realtime
            ADD TABLE public.product_stock_updates;
    END IF;
END;
$migration$;
