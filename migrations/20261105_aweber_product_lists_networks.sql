-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard,
-- ANTES do deploy do código que manda leads da BuyGoods pro AWeber.
--
-- Leads da BuyGoods pro AWeber (substitui o autoresponder nativo da
-- BuyGoods). O mesmo produto pode vender pela PagAmerican e pela BuyGoods
-- (ex: SodaHorsePeak tem um product_id só), então ligar o produto não basta:
-- cada linha diz de quais redes o produto manda lead. Assim a migração da
-- BuyGoods é feita produto a produto -- inclui 'BuyGoods' no array no mesmo
-- momento em que desliga o autoresponder daquele produto no painel da
-- BuyGoods -- sem deploy.
--
-- Os valores do array são os de NetworkType (app/models/enums.py):
-- 'BuyGoods', 'PagAmerican'.

alter table public.aweber_product_lists
  add column if not exists enabled_networks text[] not null default '{PagAmerican}';

-- Linha existente (SodaHorsePeak) segue só PagAmerican, como hoje. A lista
-- de abandono estava apontando pra SkinRenewPro Abandoned (6966538); a certa
-- é SodaHorsePeak (Abandoned) (6977004) -- já corrigida em produção em
-- 2026-11-05, repetida aqui pra o arquivo refletir o estado do banco.
update public.aweber_product_lists
set abandon_list_id = '6977004', updated_at = now()
where product_id = 'f0494709-01fb-4d0e-8e8c-3e200f25b13c'
  and abandon_list_id = '6966538';

-- Ligar a BuyGoods num produto (exemplo, SodaHorsePeak):
--   update public.aweber_product_lists
--   set enabled_networks = array_append(enabled_networks, 'BuyGoods'),
--       updated_at = now()
--   where product_id = 'f0494709-01fb-4d0e-8e8c-3e200f25b13c'
--     and not ('BuyGoods' = any(enabled_networks));
--
-- Produto novo, só BuyGoods:
--   insert into public.aweber_product_lists
--     (product_id, neworder_list_id, abandon_list_id, enabled_networks)
--   values ('<products.id>', '<lista vendas>', '<lista abandono>', '{BuyGoods}');
