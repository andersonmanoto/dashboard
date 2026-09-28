-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard,
-- DEPOIS de 20260928_create_aweber_lead_queue.sql.
--
-- No AWeber cada produto tem duas listas: uma pra New Order (vendas) e
-- outra pra carrinho abandonado. Esta tabela mapeia produto -> listas; só
-- produtos cadastrados aqui mandam lead pro AWeber. Pra liberar um produto
-- novo basta inserir uma linha (sem deploy).
--
-- O produto do lead vem do offerCode da PagAmerican (checkouts.checkout_code
-- -> product_id), tanto na venda quanto no abandono.

create table if not exists public.aweber_product_lists (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null unique references public.products(id),
  neworder_list_id text not null,
  abandon_list_id text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Lista de destino de cada lead, decidida na hora de enfileirar
alter table public.aweber_lead_queue
  add column if not exists list_id text;

-- Início: só SodaHorsePeak
insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id)
values ('f0494709-01fb-4d0e-8e8c-3e200f25b13c', '6977002', '6966538')
on conflict (product_id) do update
  set neworder_list_id = excluded.neworder_list_id,
      abandon_list_id = excluded.abandon_list_id,
      updated_at = now();
