-- Store maintenance controls and Nepal-time scheduling.

ALTER TABLE public.store_settings
    ADD COLUMN IF NOT EXISTS maintenance_mode TEXT NOT NULL DEFAULT 'off'
        CHECK (maintenance_mode IN ('off', 'now', 'scheduled', 'daily')),
    ADD COLUMN IF NOT EXISTS maintenance_starts_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS maintenance_daily_time TIME WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS maintenance_duration_minutes INTEGER
        CHECK (
            maintenance_duration_minutes IS NULL
            OR maintenance_duration_minutes BETWEEN 1 AND 43200
        ),
    ADD COLUMN IF NOT EXISTS maintenance_message TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

UPDATE public.store_settings
SET maintenance_mode = 'off'
WHERE maintenance_mode IS NULL;
