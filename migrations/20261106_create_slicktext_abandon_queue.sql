-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- ANTES do deploy do código que usa a fila (sem a tabela, o abandono não
-- vai pro SlickText -- o carrinho em si continua sendo salvo).
--
-- Fila do SMS de carrinho abandonado (mesmo padrão da aweber_lead_queue).
-- Antes o envio era uma BackgroundTask do FastAPI / chamada direta no
-- worker: sem registro, sem retry, e os descartes (país fora de US/CA,
-- telefone inválido, lista não mapeada...) só apareciam no log.
--
-- Fluxo:
--   1. O EventProcessor, ao salvar o carrinho (BuyGoods, PagAmerican,
--      DigiStore24), insere uma linha aqui com o id do carrinho.
--   2. O cron cron_send_slicktext_abandons (worker.py) lê as 'pending',
--      valida o telefone, envia pra(s) lista(s) de abandono do produto e
--      grava o resultado:
--        sent    -> enviado pra todas as contas active do produto
--        skipped -> descartado de propósito (motivo em last_error)
--        pending -> erro temporário, tenta de novo no próximo tick
--        failed  -> esgotou as tentativas

create table if not exists public.slicktext_abandon_queue (
  id uuid primary key default gen_random_uuid(),
  abandoned_cart_id uuid references public.abandoned_carts(id) on delete set null,
  network text not null,
  phone text,
  payload jsonb not null,
  status text not null default 'pending',
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  sent_at timestamptz,
  constraint slicktext_abandon_queue_status_check
    check (status in ('pending', 'sent', 'skipped', 'failed'))
);

create index if not exists idx_slicktext_abandon_queue_status
  on public.slicktext_abandon_queue (status, created_at);

create index if not exists idx_slicktext_abandon_queue_abandoned_cart_id
  on public.slicktext_abandon_queue (abandoned_cart_id);

-- Limpeza automática: o registro fica 30 dias (sent/skipped/failed).
create extension if not exists pg_cron with schema extensions;

create or replace function public.cleanup_slicktext_abandon_queue()
returns void
language sql
as $$
  delete from public.slicktext_abandon_queue
  where status in ('sent', 'skipped', 'failed')
    and updated_at < now() - interval '30 days';
$$;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'cleanup-slicktext-abandon-queue') then
    perform cron.unschedule('cleanup-slicktext-abandon-queue');
  end if;
end $$;

select cron.schedule(
  'cleanup-slicktext-abandon-queue',
  '20 3 * * *', -- 03:20 UTC, todo dia
  $$ select public.cleanup_slicktext_abandon_queue(); $$
);

-- Conferência (últimas 24h):
-- select status, left(last_error, 60) as motivo, count(*)
-- from public.slicktext_abandon_queue
-- where created_at > now() - interval '1 day'
-- group by 1, 2 order by 3 desc;
