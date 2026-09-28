-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Envio de leads da PagAmerican (vendas e carrinhos abandonados) pra lista
-- do AWeber via cron.
--
-- Fluxo (mesmo padrão da zapier_webhook_queue):
--   1. O backend (EventProcessor), ao processar um `neworder` ao vivo (não
--      teste) ou um `abandon` da PagAmerican, monta o corpo do subscriber e
--      insere uma linha aqui.
--   2. Um cron job (worker.py) lê as linhas 'pending', faz o POST
--      /subscribers no AWeber e atualiza o status.
--
-- aweber_oauth_tokens: guarda o access/refresh token do OAuth do AWeber
-- (linha única). O access token expira em ~2h e é renovado pelo worker, que
-- regrava a linha -- por isso fica no banco, e não num arquivo dentro do
-- container (que se perde a cada deploy). Carga inicial:
-- app/scripts/aweber_import_tokens.py.

create table if not exists public.aweber_lead_queue (
  id uuid primary key default gen_random_uuid(),
  lead_type text not null,
  email text not null,
  payload jsonb not null,
  status text not null default 'pending',
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  sent_at timestamptz
);

alter table public.aweber_lead_queue
  drop constraint if exists aweber_lead_queue_status_check;

alter table public.aweber_lead_queue
  add constraint aweber_lead_queue_status_check
  check (status in ('pending', 'sent', 'failed'));

create index if not exists idx_aweber_lead_queue_status
  on public.aweber_lead_queue (status, created_at);

create table if not exists public.aweber_oauth_tokens (
  id integer primary key default 1,
  access_token text not null,
  refresh_token text not null,
  expires_at timestamptz not null,
  updated_at timestamptz not null default now(),
  constraint aweber_oauth_tokens_single_row check (id = 1)
);

-- Limpeza automática da fila (mesma retenção da zapier_webhook_queue):
--   - status='sent'   -> apaga depois de 7 dias
--   - status='failed' -> apaga depois de 30 dias
create extension if not exists pg_cron with schema extensions;

create or replace function public.cleanup_aweber_lead_queue()
returns void
language sql
as $$
  delete from public.aweber_lead_queue
  where (status = 'sent' and sent_at < now() - interval '7 days')
     or (status = 'failed' and updated_at < now() - interval '30 days');
$$;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'cleanup-aweber-lead-queue') then
    perform cron.unschedule('cleanup-aweber-lead-queue');
  end if;
end $$;

select cron.schedule(
  'cleanup-aweber-lead-queue',
  '10 3 * * *', -- 03:10 UTC, todo dia
  $$ select public.cleanup_aweber_lead_queue(); $$
);
