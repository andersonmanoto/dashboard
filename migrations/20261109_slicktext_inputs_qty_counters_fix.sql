-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Correção da 20261109_slicktext_inputs_qty_counters.sql: qty_synced e
-- qty_failed já existiam em slicktext_inputs (nullable, sem default), então o
-- "add column if not exists" não aplicou o "not null default 0". Input novo
-- nascia com null e o trigger (qty + 1) mantinha null pra sempre.
--
-- Recalcula os dois contadores de todos os inputs a partir da fila e força
-- default 0 + not null. Mesma trava da migration original.

begin;

lock table public.slicktext_input_queue in share mode;

update public.slicktext_inputs i
set qty_synced = (
      select count(*) from public.slicktext_input_queue q
      where q.input_id = i.id and q.status = 'synced'
    ),
    qty_failed = (
      select count(*) from public.slicktext_input_queue q
      where q.input_id = i.id and q.status = 'failed'
    );

alter table public.slicktext_inputs
  alter column qty_synced set default 0,
  alter column qty_synced set not null,
  alter column qty_failed set default 0,
  alter column qty_failed set not null;

commit;
