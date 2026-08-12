-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Suporte ao endpoint POST /active-funnels/sync.
--
-- Fluxo atual:
--   1. Uma Edge Function do Supabase insere a linha em
--      product_network_active_funnels (active_funnel_number, activated_by,
--      activated_at) ANTES de chamar o backend. O frontend manda o id dessa
--      linha (`product_network_active_funnels_id`) no payload, junto com o
--      funil anterior (`previous_funnel`).
--   2. O backend NÃO insere/faz upsert em product_network_active_funnels —
--      só dá UPDATE de status/error_message na linha indicada, ao final da
--      tentativa de sync via SFTP.
--   3. O backend continua dono de `active_funnel_history` (grava a
--      transição quando o funil realmente muda, em caso de sucesso).
--
-- Tabelas já criadas pelo time de frontend (não recriar):
--
-- create table public.product_network_active_funnels (
--   id uuid not null default gen_random_uuid(),
--   product_id uuid not null references products(id) on delete cascade,
--   network_id uuid not null references networks(id) on delete cascade,
--   active_funnel_number integer not null,
--   activated_by uuid references profiles(id) on delete set null,
--   activated_at timestamptz not null default now(),
--   updated_at timestamptz not null default now(),
--   unique (product_id, network_id)
-- );
-- (+ trigger update_updated_at_column em updated_at)
--
-- create table public.active_funnel_history (
--   id uuid not null default gen_random_uuid(),
--   product_id uuid not null references products(id) on delete restrict,
--   network_id uuid not null references networks(id) on delete restrict,
--   active_funnel_number integer null,
--   previous_active_funnel_number integer null,
--   changed_by uuid not null references profiles(id) on delete restrict,
--   changed_at timestamptz not null default now()
-- );

-- 1) Colunas novas em product_network_active_funnels, pro backend reportar
--    o resultado da tentativa de sync (a Edge Function insere a linha sem
--    essas colunas preenchidas; ficam null até o backend processar).
alter table product_network_active_funnels
  add column if not exists status text,
  add column if not exists error_message text;

alter table product_network_active_funnels
  drop constraint if exists product_network_active_funnels_status_check;

alter table product_network_active_funnels
  add constraint product_network_active_funnels_status_check
  check (status is null or status in ('success', 'error'));

-- 2) Remove a coluna antiga de products — só rode depois que o endpoint
--    novo (que usa product_network_active_funnels) já estiver em produção.
alter table products
  drop column if exists active_funnel_number;

-- 3) checkouts.switch_link vira redundante: o backend passa a derivar o id
--    da tag <a> como f"switchLink-{quantity}b" a partir de
--    checkouts.quantity, em vez de ler de uma coluna preenchida manualmente.
--    Convenção alinhada com quem cria os codenames dos produtos — sem mais
--    exceções tipo "buy6free3" (Prostafense), esses codenames antigos serão
--    padronizados. Só rode depois que o endpoint novo (que já deriva
--    switch_link) estiver em produção.
alter table checkouts
  drop column if exists switch_link;
