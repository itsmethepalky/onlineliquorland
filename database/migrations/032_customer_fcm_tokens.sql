CREATE TABLE IF NOT EXISTS public.customer_push_tokens (
    device_id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    token TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS customer_push_tokens_user_id_idx
    ON public.customer_push_tokens(user_id);

ALTER TABLE public.customer_push_tokens ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.customer_push_tokens
    FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.customer_push_tokens
    TO service_role;
