-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- ANTES do deploy do worker.
--
-- slicktext_sync_queue passa a ter uma linha por lead e por lista, cada uma
-- com status e retry próprios.
--
-- Fluxo:
--   1. O trigger (venda) e o EventProcessor (abandono) continuam criando uma
--      linha por lead, com list_id vazio.
--   2. O cron valida o telefone uma vez e busca as listas das contas active.
--      A linha original fica com a primeira lista e o cron cria uma linha
--      para cada lista restante, que sai na rodada seguinte.
--   3. list_id preenchido = telefone já validado: a linha só envia pra lista
--      dela. Se a conta caiu nesse meio-tempo, vira skipped.
--
-- Linhas antigas ficam com list_id vazio: já foram enviadas pra todas as
-- contas, ou são pendentes que o cron divide na próxima rodada.
--
-- O código atual continua funcionando depois desta migration.

begin;

-- 1. Colunas -------------------------------------------------------------------

alter table public.slicktext_sync_queue
  add column if not exists list_id text,
  add column if not exists slick_account_id uuid
    references public.slicktext_accounts(id) on delete set null;

create index if not exists idx_slicktext_sync_queue_account
  on public.slicktext_sync_queue (slick_account_id, created_at);

-- 2. Unique: várias linhas por lead, uma por lista --------------------------------

alter table public.slicktext_sync_queue
  drop constraint if exists slicktext_sync_queue_event_id_key;

-- Uma linha sem lista por venda: é o arbiter do ON CONFLICT do trigger
create unique index if not exists uq_slicktext_sync_queue_event_lead
  on public.slicktext_sync_queue (event_id)
  where list_id is null;

create unique index if not exists uq_slicktext_sync_queue_event_list
  on public.slicktext_sync_queue (event_id, list_id);

create unique index if not exists uq_slicktext_sync_queue_cart_list
  on public.slicktext_sync_queue (abandoned_cart_id, list_id);

-- 3. Trigger: ON CONFLICT aponta pro índice parcial --------------------------------

-- Reescreve a partir da definição atual pra manter os outros atributos da
-- função, que foi criada fora das migrations. Sem isso, o insert em events
-- falharia, então aborta se o trecho esperado não estiver lá.
do $$
declare
  v_def text := pg_get_functiondef('public.enqueue_slicktext_approved_order()'::regprocedure);
begin
  if v_def not like '%ON CONFLICT (event_id) DO NOTHING%' then
    raise exception 'enqueue_slicktext_approved_order: ON CONFLICT não encontrado';
  end if;

  execute replace(
    v_def,
    'ON CONFLICT (event_id) DO NOTHING',
    'ON CONFLICT (event_id) WHERE list_id IS NULL DO NOTHING'
  );
end $$;

commit;

notify pgrst, 'reload schema';

-- Conferência (últimas 24h):
-- select q.lead_type, a.name as conta, q.list_id, q.status, count(*)
-- from public.slicktext_sync_queue q
-- left join public.slicktext_accounts a on a.id = q.slick_account_id
-- where q.created_at > now() - interval '1 day'
-- group by 1, 2, 3, 4 order by 1, 2, 3;
