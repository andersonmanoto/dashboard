-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- ANTES do deploy do código que grava essas colunas. Sem as colunas, o
-- insert em abandoned_carts falha e o carrinho se perde.
--
-- 1. abandoned_carts ganha product_id e network_id (antes só tinha o
--    product_codename, e pra saber o produto era preciso cruzar com
--    checkouts toda vez).
-- 2. aweber_lead_queue ganha a origem de cada lead: abandoned_cart_id (lead
--    de abandono) ou event_id (lead de venda).
--
-- As colunas são nullable: carrinho de codename não cadastrado em checkouts
-- fica sem product_id, e linhas antigas da fila ficam sem origem (a fila é
-- limpa em 7/30 dias, então não vale fazer backfill dela).

-- 1. abandoned_carts.product_id / network_id ---------------------------------

alter table public.abandoned_carts
  add column if not exists product_id uuid
    references public.products(id) on delete set null,
  add column if not exists network_id uuid
    references public.networks(id);

create index if not exists idx_abandoned_carts_product_id
  on public.abandoned_carts (product_id, created_at);

create index if not exists idx_abandoned_carts_network_id
  on public.abandoned_carts (network_id, created_at);

-- Backfill da rede pelo formato do payload de cada rota:
--   PagAmerican: tem _pa_event (checkout.session.abandoned)
--   DigiStore24: order_id "CART-<n>" + first_name
--   BuyGoods:    o resto (form flat com emailaddress / payloads de IPN)
update public.abandoned_carts ac
set network_id = n.id
from public.networks n
where ac.network_id is null
  and n.name = case
    when ac.payload ? '_pa_event' then 'PagAmerican'
    when ac.payload->>'order_id' like 'CART-%'
      and ac.payload ? 'first_name' then 'DigiStore24'
    else 'BuyGoods'
  end;

-- Backfill do produto pelo checkout (código + rede, e account_id quando o
-- payload tem -- o mesmo código existe em mais de uma conta da BuyGoods).
-- Só preenche quando o match aponta pra um único produto; ambíguo fica null.
with matches as (
  select ac.id as cart_id,
         min(c.product_id::text)::uuid as product_id
  from public.abandoned_carts ac
  join public.checkouts c
    on c.checkout_code = ac.product_codename
   and c.network_id = ac.network_id
   and (ac.payload->>'account_id' is null
        or c.account_id = ac.payload->>'account_id')
  where ac.product_id is null
    and coalesce(ac.product_codename, '') <> ''
  group by ac.id
  having count(distinct c.product_id) = 1
)
update public.abandoned_carts ac
set product_id = m.product_id
from matches m
where ac.id = m.cart_id;

-- 2. aweber_lead_queue.abandoned_cart_id / event_id --------------------------

alter table public.aweber_lead_queue
  add column if not exists abandoned_cart_id uuid
    references public.abandoned_carts(id) on delete set null,
  add column if not exists event_id uuid
    references public.events(id) on delete set null;

create index if not exists idx_aweber_lead_queue_abandoned_cart_id
  on public.aweber_lead_queue (abandoned_cart_id);

create index if not exists idx_aweber_lead_queue_event_id
  on public.aweber_lead_queue (event_id);

-- Conferência:
-- select n.name, count(*) as carts, count(ac.product_id) as com_produto
-- from public.abandoned_carts ac
-- left join public.networks n on n.id = ac.network_id
-- group by n.name order by carts desc;
