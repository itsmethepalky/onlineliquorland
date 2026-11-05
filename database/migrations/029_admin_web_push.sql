-- Keep admin browser subscriptions separate from customer accounts and
-- allow delivery after the administrator has signed out.

ALTER TABLE public.admin_push_tokens
    ADD COLUMN IF NOT EXISTS browser_id TEXT,
    ADD COLUMN IF NOT EXISTS platform TEXT NOT NULL DEFAULT 'android',
    ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'admin',
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS admin_notification_eligible
        BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE public.admin_push_tokens AS token
SET browser_id = 'legacy-' || md5(token.token)
WHERE token.browser_id IS NULL;

UPDATE public.admin_push_tokens AS token
SET role = CASE
        WHEN profile.role::TEXT IN ('admin', 'staff')
            THEN profile.role::TEXT
        ELSE 'staff'
    END,
    is_active = COALESCE(profile.is_active, FALSE),
    admin_notification_eligible =
        profile.role = 'admin' AND COALESCE(profile.is_active, FALSE),
    platform = CASE
        WHEN token.platform = 'android' THEN 'android'
        ELSE token.platform
    END
FROM public.profiles AS profile
WHERE profile.id = token.user_id;

ALTER TABLE public.admin_push_tokens
    ALTER COLUMN browser_id SET NOT NULL;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.admin_push_tokens'::regclass
          AND conname = 'admin_push_tokens_role_check'
    ) THEN
        ALTER TABLE public.admin_push_tokens
            ADD CONSTRAINT admin_push_tokens_role_check
            CHECK (role IN ('admin', 'staff'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.admin_push_tokens'::regclass
          AND conname = 'admin_push_tokens_platform_check'
    ) THEN
        ALTER TABLE public.admin_push_tokens
            ADD CONSTRAINT admin_push_tokens_platform_check
            CHECK (platform IN ('web', 'android', 'ios'));
    END IF;
END;
$migration$;

CREATE UNIQUE INDEX IF NOT EXISTS admin_push_tokens_user_browser_idx
    ON public.admin_push_tokens (user_id, browser_id);

CREATE INDEX IF NOT EXISTS admin_push_tokens_eligible_admin_idx
    ON public.admin_push_tokens (user_id, browser_id)
    WHERE role = 'admin'
      AND is_active
      AND admin_notification_eligible;

CREATE TABLE IF NOT EXISTS public.admin_order_push_deliveries (
    order_id BIGINT NOT NULL
        REFERENCES public.orders(id) ON DELETE CASCADE,
    browser_id TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN ('sending', 'sent', 'failed')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 1
        CHECK (attempt_count > 0),
    error_code TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (order_id, browser_id),
    CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);

ALTER TABLE public.admin_order_push_deliveries
    ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.admin_order_push_deliveries
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE
    ON TABLE public.admin_order_push_deliveries
    TO service_role;

CREATE OR REPLACE FUNCTION public.claim_admin_order_push_delivery(
    p_order_id BIGINT,
    p_browser_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    v_claimed BOOLEAN;
BEGIN
    INSERT INTO public.admin_order_push_deliveries AS delivery (
        order_id,
        browser_id,
        status,
        claimed_at,
        sent_at,
        attempt_count,
        error_code,
        updated_at
    )
    VALUES (
        p_order_id,
        p_browser_id,
        'sending',
        NOW(),
        NULL,
        1,
        NULL,
        NOW()
    )
    ON CONFLICT (order_id, browser_id) DO UPDATE
    SET status = 'sending',
        claimed_at = NOW(),
        sent_at = NULL,
        attempt_count = delivery.attempt_count + 1,
        error_code = NULL,
        updated_at = NOW()
    WHERE delivery.status = 'failed'
       OR (
            delivery.status = 'sending'
            AND delivery.claimed_at < NOW() - INTERVAL '5 minutes'
       )
    RETURNING TRUE INTO v_claimed;

    RETURN COALESCE(v_claimed, FALSE);
END;
$function$;

CREATE OR REPLACE FUNCTION public.finish_admin_order_push_delivery(
    p_order_id BIGINT,
    p_browser_id TEXT,
    p_sent BOOLEAN,
    p_error_code TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
BEGIN
    UPDATE public.admin_order_push_deliveries
    SET status = CASE WHEN p_sent THEN 'sent' ELSE 'failed' END,
        sent_at = CASE WHEN p_sent THEN NOW() ELSE NULL END,
        error_code = CASE
            WHEN p_sent THEN NULL
            ELSE LEFT(COALESCE(p_error_code, 'delivery_failed'), 80)
        END,
        updated_at = NOW()
    WHERE order_id = p_order_id
      AND browser_id = p_browser_id
      AND status = 'sending';

    RETURN FOUND;
END;
$function$;

REVOKE ALL ON FUNCTION public.claim_admin_order_push_delivery(BIGINT, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finish_admin_order_push_delivery(
    BIGINT, TEXT, BOOLEAN, TEXT
)
    FROM PUBLIC, anon, authenticated;

GRANT EXECUTE ON FUNCTION public.claim_admin_order_push_delivery(BIGINT, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.finish_admin_order_push_delivery(
    BIGINT, TEXT, BOOLEAN, TEXT
)
    TO service_role;

NOTIFY pgrst, 'reload schema';
