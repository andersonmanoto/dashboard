-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard,
-- e faça o deploy do backend logo em seguida (o upsert passa a usar
-- on_conflict=network,event_order_id,conversion_type).
--
-- A chave única antiga (network, bg_order_id, conversion_type) colidia entre
-- contas vendor da BuyGoods: o order_id numérico do IPN é sequencial POR
-- CONTA (ex: 123522 existe na conta 12837 e na 13982), então a segunda venda
-- era ignorada como "já está na fila" e nunca ia pro RedTrack (casos em
-- 09/11 e 10/11). O event_order_id (order_id_global da BuyGoods, order_id da
-- DigiStore24) é único na rede.

-- Checagem: tem que voltar 0 linhas antes de criar a chave nova.
--   select network, event_order_id, conversion_type, count(*)
--   from public.redtrack_conversion_queue
--   group by 1, 2, 3 having count(*) > 1 or event_order_id is null;

alter table public.redtrack_conversion_queue
  alter column event_order_id set not null;

alter table public.redtrack_conversion_queue
  drop constraint if exists redtrack_conversion_queue_order_type_key;

alter table public.redtrack_conversion_queue
  drop constraint if exists redtrack_conversion_queue_event_order_type_key;

alter table public.redtrack_conversion_queue
  add constraint redtrack_conversion_queue_event_order_type_key
  unique (network, event_order_id, conversion_type);
