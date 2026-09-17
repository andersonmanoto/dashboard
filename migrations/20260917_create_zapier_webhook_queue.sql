-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Suporte ao envio de leads (novos pedidos e carrinhos abandonados) pro
-- webhook do Zapier via cron.
--
-- Fluxo:
--   1. O backend (EventProcessor), ao processar um `neworder` ao vivo (não
--      teste, não backfill/retro) ou um `abandon` da BuyGoods, já monta o
--      JSON final no formato exigido pelo Zapier e insere uma linha aqui.
--   2. Um cron job (worker.py) lê as linhas 'pending', faz o POST pro
--      Zapier e atualiza o status. Isso mantém o caminho do webhook rápido
--      (só um insert, sem chamada HTTP externa síncrona).

create table if not exists public.zapier_webhook_queue (
  id uuid primary key default gen_random_uuid(),
  payload jsonb not null,
  status text not null default 'pending',
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  sent_at timestamptz
);

alter table public.zapier_webhook_queue
  drop constraint if exists zapier_webhook_queue_status_check;

alter table public.zapier_webhook_queue
  add constraint zapier_webhook_queue_status_check
  check (status in ('pending', 'sent', 'failed'));

create index if not exists idx_zapier_webhook_queue_status
  on public.zapier_webhook_queue (status, created_at);
