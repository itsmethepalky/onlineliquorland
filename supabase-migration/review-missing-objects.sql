-- REVIEW ONLY: generated from the old project's inventory.
-- Do not execute until dependencies and RLS status are checked.

-- Missing RLS policies
CREATE POLICY "brands_public_read_active" ON "public"."brands" AS PERMISSIVE FOR SELECT TO PUBLIC
USING ((is_active = true));

CREATE POLICY "categories_public_read_active" ON "public"."categories" AS PERMISSIVE FOR SELECT TO PUBLIC
USING ((is_active = true));

CREATE POLICY "products_public_read_active" ON "public"."products" AS PERMISSIVE FOR SELECT TO PUBLIC
USING ((is_active = true));

CREATE POLICY "product_images_public_read" ON "storage"."objects" AS PERMISSIVE FOR SELECT TO PUBLIC
USING ((bucket_id = 'product-images'::text));

CREATE POLICY "product_images_staff_delete" ON "storage"."objects" AS PERMISSIVE FOR DELETE TO PUBLIC
USING (((bucket_id = 'product-images'::text) AND private.is_staff()));

CREATE POLICY "product_images_staff_update" ON "storage"."objects" AS PERMISSIVE FOR UPDATE TO PUBLIC
USING (((bucket_id = 'product-images'::text) AND private.is_staff()));

CREATE POLICY "product_images_staff_write" ON "storage"."objects" AS PERMISSIVE FOR INSERT TO PUBLIC
WITH CHECK (((bucket_id = 'product-images'::text) AND private.is_staff()));

-- Missing function definitions
CREATE OR REPLACE FUNCTION public.update_store_customer_phone(p_user_id uuid, p_phone text)
 RETURNS boolean
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
            declare
                v_phone text;
                v_updated_rows integer;
                v_profile_role text;
                v_profile_active boolean;
            begin
                v_phone := trim(coalesce(p_phone, ''));
                if length(v_phone) < 7 or length(v_phone) > 30 then
                    raise exception
                        'Phone number must be 7 to 30 characters';
                end if;

                select p.role::text, p.is_active
                into v_profile_role, v_profile_active
                from public.profiles as p
                where p.id = p_user_id
                for update;

                if not found
                   or v_profile_role <> 'customer'
                   or not v_profile_active then
                    raise exception
                        'Active customer profile not found';
                end if;

                perform 1
                from public.customers as c
                where c.user_id = p_user_id
                for update;

                if not found then
                    raise exception
                        'Customer account not found';
                end if;

                update public.profiles
                set phone = v_phone
                where id = p_user_id;

                get diagnostics v_updated_rows = row_count;
                if v_updated_rows <> 1 then
                    raise exception
                        'Could not update customer profile phone';
                end if;

                update public.customers
                set phone = v_phone
                where user_id = p_user_id;

                get diagnostics v_updated_rows = row_count;
                if v_updated_rows <> 1 then
                    raise exception
                        'Could not update customer account phone';
                end if;

                return true;
            end;
            $function$
