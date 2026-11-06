-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- ANTES do deploy do código que usa a fila unificada.
--
-- Unifica as filas do SlickText: slicktext_sync_queue passa a guardar vendas
-- e carrinhos abandonados, como a aweber_lead_queue (lead_type + origem em
-- event_id ou abandoned_cart_id). slicktext_abandon_queue sai na parte 2.
--
-- Ordem:
--   1. esta migration (o código antigo continua funcionando com ela)
--   2. deploy do worker, depois da API (worker antigo não sabe ignorar
--      linha de abandono)
--   3. 20261106_slicktext_queue_unify_2_drop_abandon_queue.sql
--
-- O trigger trg_enqueue_slicktext (events) não muda: o insert dele só passa
-- event_id, lead_type cai no default e o ON CONFLICT (event_id) usa o unique
-- criado aqui.

begin;

-- 1. Colunas -------------------------------------------------------------------

alter table public.slicktext_sync_queue
  add column if not exists id uuid not null default gen_random_uuid(),
  add column if not exists lead_type text not null default 'neworder',
  add column if not exists abandoned_cart_id uuid
    references public.abandoned_carts(id) on delete set null,
  add column if not exists network text,
  add column if not exists phone text,
  add column if not exists payload jsonb,
  add column if not exists sent_at timestamptz;

-- 2. PK: event_id -> id (event_id fica unique e nullable) ------------------------

alter table public.slicktext_sync_queue
  add constraint slicktext_sync_queue_event_id_key unique (event_id);

do $$
declare
  v_pk text;
begin
  select conname into v_pk
  from pg_constraint
  where conrelid = 'public.slicktext_sync_queue'::regclass and contype = 'p';

  if v_pk is not null then
    execute format('alter table public.slicktext_sync_queue drop constraint %I', v_pk);
  end if;
end $$;

alter table public.slicktext_sync_queue alter column event_id drop not null;
alter table public.slicktext_sync_queue add primary key (id);

-- 3. Checks ----------------------------------------------------------------------

-- A tabela foi criada fora das migrations: tira qualquer check antigo de status
do $$
declare
  v_check text;
begin
  for v_check in
    select conname
    from pg_constraint
    where conrelid = 'public.slicktext_sync_queue'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) ilike '%status%'
  loop
    execute format('alter table public.slicktext_sync_queue drop constraint %I', v_check);
  end loop;
end $$;

alter table public.slicktext_sync_queue
  add constraint slicktext_sync_queue_status_check
    check (status in ('pending', 'retry', 'synced', 'skipped', 'failed')),
  add constraint slicktext_sync_queue_lead_type_check
    check (lead_type in ('neworder', 'abandon')),
  add constraint slicktext_sync_queue_neworder_event_check
    check (lead_type <> 'neworder' or event_id is not null);

-- 4. Índices ---------------------------------------------------------------------

create index if not exists idx_slicktext_sync_queue_pending
  on public.slicktext_sync_queue (lead_type, status, created_at);

create index if not exists idx_slicktext_sync_queue_abandoned_cart_id
  on public.slicktext_sync_queue (abandoned_cart_id);

-- 5. Limpeza ---------------------------------------------------------------------

-- O job antigo apagava tudo com 3 dias. Venda continua assim; abandono fica
-- 30 dias, como na slicktext_abandon_queue, e pending não é apagado.
create or replace function public.cleanup_slicktext_sync_queue()
returns void
language sql
as $$
  delete from public.slicktext_sync_queue
  where (lead_type = 'neworder' and updated_at < now() - interval '3 days')
     or (lead_type = 'abandon'
         and status in ('synced', 'skipped', 'failed')
         and updated_at < now() - interval '30 days');
$$;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'cleanup-slicktext-sync-queue') then
    perform cron.unschedule('cleanup-slicktext-sync-queue');
  end if;
end $$;

select cron.schedule(
  'cleanup-slicktext-sync-queue',
  '0 3 * * *', -- 03:00 UTC, todo dia
  $$ select public.cleanup_slicktext_sync_queue(); $$
);

commit;

notify pgrst, 'reload schema';
