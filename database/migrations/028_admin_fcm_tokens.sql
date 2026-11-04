create table if not exists public.admin_push_tokens (
    token text primary key,
    user_id uuid not null references public.profiles(id) on delete cascade,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists admin_push_tokens_user_id_idx
    on public.admin_push_tokens(user_id);

alter table public.admin_push_tokens enable row level security;

revoke all on table public.admin_push_tokens
    from public, anon, authenticated;

grant select, insert, update, delete on table public.admin_push_tokens
    to service_role;
