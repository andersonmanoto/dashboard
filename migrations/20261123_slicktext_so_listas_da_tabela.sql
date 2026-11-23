-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard,
-- DEPOIS de 20261123_slicktext_last_error_unificado.sql e do deploy do código.
--
-- SMS só vai pras listas da slicktext_product_lists. Produto sem lista lá não
-- é erro: o lead nem entra na fila (igual à AWeber com aweber_product_lists).
--
--   1. Trigger na slicktext_sync_queue: venda (neworder, sem list_id) de
--      produto sem approved_list_id não é inserida. O trigger de events
--      (enqueue_slicktext_approved_order, criado fora das migrations) não muda.
--      Venda sem product_id continua entrando: o cron grava
--      produto_nao_identificado, que é pra corrigir.
--      O abandono é filtrado no EventProcessor (_enqueue_slicktext_abandon).
--   2. Snapshot: tira dos erros os leads que eram "lista não mapeada".
--   3. Fila: apaga essas linhas; venda sem produto vira produto_nao_identificado.

begin;

-- 1. Trigger ----------------------------------------------------------------------

create or replace function public.slicktext_queue_only_mapped_products()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_product_id uuid;
begin
  if new.lead_type <> 'neworder' or new.list_id is not null or new.event_id is null then
    return new;
  end if;

  select e.product_id into v_product_id from public.events e where e.id = new.event_id;

  if v_product_id is not null and not exists (
    select 1
    from public.slicktext_product_lists pl
    where pl.product_id = v_product_id
      and pl.approved_list_id is not null
  ) then
    return null;  -- produto sem lista: não enfileira
  end if;

  return new;
end;
$$;

drop trigger if exists trg_slicktext_queue_only_mapped_products on public.slicktext_sync_queue;
create trigger trg_slicktext_queue_only_mapped_products
  before insert on public.slicktext_sync_queue
  for each row execute function public.slicktext_queue_only_mapped_products();

-- 2. Snapshot -----------------------------------------------------------------------

-- Leads "lista não mapeada" que ainda estão na fila, no mesmo recorte da
-- snapshot (dia do primeiro created_at em São Paulo, rede, produto).
with sem_lista as (
  select
    (min(q.created_at) at time zone 'America/Sao_Paulo')::date as snapshot_date,
    q.lead_type,
    coalesce(e.network_id, ac.network_id, q.network_id) as network_id,
    coalesce(e.product_id, ac.product_id) as product_id
  from public.slicktext_sync_queue q
  left join public.events e on e.id = q.event_id
  left join public.abandoned_carts ac on ac.id = q.abandoned_cart_id
  where (q.last_error in ('lista_nao_encontrada', 'lista_nao_mapeada')
         or q.last_error like 'lista de abandono não mapeada:%')
    and coalesce(e.product_id, ac.product_id) is not null
  group by q.lead_type,
           coalesce(q.event_id::text, q.abandoned_cart_id::text, q.id::text),
           3, 4
)
update public.snapshot_daily_leads_sms s
set qty_neworder_error = greatest(0, s.qty_neworder_error - x.n_neworder),
    qty_abandon_error  = greatest(0, s.qty_abandon_error - x.n_abandon),
    updated_at = now()
from (
  select snapshot_date, network_id, product_id,
         count(*) filter (where lead_type = 'neworder') as n_neworder,
         count(*) filter (where lead_type = 'abandon')  as n_abandon
  from sem_lista
  group by 1, 2, 3
) x
where s.snapshot_date = x.snapshot_date
  and s.product_id = x.product_id
  and s.network_id is not distinct from x.network_id;

-- A fila de venda é limpa em 3 dias: vendas antigas de produto sem lista já
-- saíram dela, mas continuam como erro na snapshot. Venda de produto sem
-- approved_list_id só falha por falta de lista (é a primeira checagem).
update public.snapshot_daily_leads_sms s
set qty_neworder_error = 0, updated_at = now()
where s.qty_neworder_success = 0
  and s.qty_neworder_error > 0
  and s.product_id is not null
  and not exists (
    select 1 from public.slicktext_product_lists pl
    where pl.product_id = s.product_id and pl.approved_list_id is not null
  );

delete from public.snapshot_daily_leads_sms
where qty_neworder_success = 0 and qty_neworder_error = 0
  and qty_abandon_success = 0 and qty_abandon_error = 0;

-- 3. Fila ---------------------------------------------------------------------------

-- Venda sem produto: não é falta de lista, é evento sem product_id
update public.slicktext_sync_queue q
set last_error = 'produto_nao_identificado'
from public.events e
where e.id = q.event_id
  and e.product_id is null
  and q.lead_type = 'neworder'
  and q.last_error in ('lista_nao_encontrada', 'lista_nao_mapeada');

delete from public.slicktext_sync_queue
where last_error in ('lista_nao_encontrada', 'lista_nao_mapeada')
   or last_error like 'lista de abandono não mapeada:%';

commit;

-- Conferência:
-- select last_error, count(*) from public.slicktext_sync_queue
-- where last_error ilike '%lista%' group by 1;   -- deve voltar vazio
-- select tgname from pg_trigger
-- where tgrelid = 'public.slicktext_sync_queue'::regclass and not tgisinternal;
