-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- ANTES do deploy do código que grava network_id.
--
-- slicktext_sync_queue: a rede do abandono passa de texto (network) pra FK
-- (network_id -> networks), como em abandoned_carts.
--
-- Ordem:
--   1. esta migration (o código atual continua gravando network)
--   2. deploy do worker, depois da API
--   3. 20261106_slicktext_queue_network_id_2_drop_network.sql
--
-- Venda fica sem network_id: a rede dela vem do evento (event_id).

begin;

alter table public.slicktext_sync_queue
  add column if not exists network_id uuid references public.networks(id);

create index if not exists idx_slicktext_sync_queue_network_id
  on public.slicktext_sync_queue (network_id, created_at);

update public.slicktext_sync_queue q
set network_id = n.id
from public.networks n
where q.network_id is null
  and n.name = q.network;

commit;

notify pgrst, 'reload schema';
