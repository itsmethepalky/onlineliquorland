-- ============================================================
-- BLUE MOON STORE
-- MIGRATION 021: PRODUCT IMAGE STORAGE
--
-- Store product images in a public-read bucket. Only active staff
-- may upload, update, or delete objects in this bucket.
-- ============================================================

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM storage.buckets
        WHERE id = 'product-images'
    ) THEN
        INSERT INTO storage.buckets (id, name, public)
        VALUES ('product-images', 'product-images', TRUE);
    ELSIF NOT EXISTS (
        SELECT 1
        FROM storage.buckets
        WHERE id = 'product-images'
          AND public = TRUE
    ) THEN
        RAISE EXCEPTION
            'The product-images bucket already exists but is not public; review its settings before applying this migration';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'product_images_public_read'
    ) THEN
        CREATE POLICY product_images_public_read
        ON storage.objects
        FOR SELECT
        TO PUBLIC
        USING (bucket_id = 'product-images');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'product_images_staff_write'
    ) THEN
        CREATE POLICY product_images_staff_write
        ON storage.objects
        FOR INSERT
        TO authenticated
        WITH CHECK (
            bucket_id = 'product-images'
            AND private.is_staff()
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'product_images_staff_update'
    ) THEN
        CREATE POLICY product_images_staff_update
        ON storage.objects
        FOR UPDATE
        TO authenticated
        USING (
            bucket_id = 'product-images'
            AND private.is_staff()
        )
        WITH CHECK (
            bucket_id = 'product-images'
            AND private.is_staff()
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'storage'
          AND tablename = 'objects'
          AND policyname = 'product_images_staff_delete'
    ) THEN
        CREATE POLICY product_images_staff_delete
        ON storage.objects
        FOR DELETE
        TO authenticated
        USING (
            bucket_id = 'product-images'
            AND private.is_staff()
        );
    END IF;
END;
$migration$;
