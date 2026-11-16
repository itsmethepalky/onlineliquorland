-- Public Auth signups must never choose their application role through
-- user-editable metadata. Provision staff and admins through a trusted process.
CREATE OR REPLACE FUNCTION private.handle_new_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.profiles (id, full_name, role, phone, email)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
    'customer'::public.user_role,
    NEW.raw_user_meta_data->>'phone',
    NEW.email
  );

  INSERT INTO public.customers (user_id, name, phone, email, address)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'full_name', split_part(NEW.email, '@', 1)),
    NEW.raw_user_meta_data->>'phone',
    NEW.email,
    NEW.raw_user_meta_data->>'address'
  );

  RETURN NEW;
END;
$$;

-- RLS controls which profile rows can be updated; column grants additionally
-- prevent users from changing role, active status, or other protected fields.
REVOKE UPDATE ON TABLE public.profiles FROM PUBLIC, anon, authenticated;
REVOKE UPDATE (id, full_name, email, role, phone, is_active, created_at)
  ON TABLE public.profiles FROM PUBLIC, anon, authenticated;
GRANT UPDATE (full_name, phone) ON TABLE public.profiles TO authenticated;
