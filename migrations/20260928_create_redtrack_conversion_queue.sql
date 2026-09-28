-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Fila de conversões pro RedTrack (substitui a ponte de terceiro que manda
-- as vendas da BuyGoods pro RedTrack).
--
-- Fluxo:
--   1. O backend (EventProcessor), ao processar um `neworder` ao vivo da
--      BuyGoods, insere uma linha aqui SÓ se for venda de front (Purchase) de
--      um par (produto, aff_id) cadastrado em internal_aff_ids. Venda de
--      afiliado externo nem entra na fila.
--   2. Um cron job (worker.py) lê as linhas 'pending', faz o POST na API do
--      RedTrack (POST /conversions) e atualiza o status.
--
-- Estar na tabela = ENFILEIRADO, não enviado. Quem diz se foi é o status:
--   pending  na fila (ou devolvida pra reenviar: status='pending', attempts=0)
--   sent     RedTrack aceitou a requisição (201). O processamento lá é
--            assíncrono: a confirmação real é cruzar com GET /conversions.
--   failed   esgotou as tentativas, precisa de ação manual
--   skipped  não dá pra enviar (ver skip_reason), ex: clickid que não é do
--            RedTrack (fbclid/gclid) ou codename não cadastrado em checkouts
--
-- Sem limpeza automática (diferente da zapier_webhook_queue): a tabela é o
-- registro do que foi enviado, e a chave única impede reenvio em dobro
-- (Purchase no RedTrack está em "create new conversion": duplicaria).

create table if not exists public.redtrack_conversion_queue (
  id uuid primary key default gen_random_uuid(),
  event_id uuid,
  network text not null,
  bg_order_id text not null,      -- order_id numérico do IPN (não o events.order_id)
  event_order_id text,            -- events.order_id (ex: "AM3Z2UPN"), pra cruzar com a events
  conversion_type text not null,  -- Purchase (só front, por enquanto)
  click_id text,
  payload jsonb not null,         -- item exato enviado no POST /conversions
  status text not null default 'pending',
  skip_reason text,
  attempts integer not null default 0,
  last_error text,
  response text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  sent_at timestamptz
);

alter table public.redtrack_conversion_queue
  drop constraint if exists redtrack_conversion_queue_status_check;

alter table public.redtrack_conversion_queue
  add constraint redtrack_conversion_queue_status_check
  check (status in ('pending', 'sent', 'failed', 'skipped'));

alter table public.redtrack_conversion_queue
  drop constraint if exists redtrack_conversion_queue_order_type_key;

alter table public.redtrack_conversion_queue
  add constraint redtrack_conversion_queue_order_type_key
  unique (network, bg_order_id, conversion_type);

create index if not exists idx_redtrack_conversion_queue_status
  on public.redtrack_conversion_queue (status, created_at);
