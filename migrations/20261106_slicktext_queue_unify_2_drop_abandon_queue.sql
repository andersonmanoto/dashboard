-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- DEPOIS do deploy do worker e da API com a fila unificada.
--
-- Copia o que está na slicktext_abandon_queue pra slicktext_sync_queue
-- (inclusive pending, que o cron novo passa a enviar) e apaga a tabela antiga.

begin;

insert into public.slicktext_sync_queue (
  id, lead_type, abandoned_cart_id, network, phone, payload,
  status, attempts, last_error, created_at, updated_at, sent_at
)
select
  id, 'abandon', abandoned_cart_id, network, phone, payload,
  case when status = 'sent' then 'synced' else status end,
  attempts, last_error, created_at, updated_at, sent_at
from public.slicktext_abandon_queue
on conflict (id) do nothing;

do $$
begin
  if exists (select 1 from cron.job where jobname = 'cleanup-slicktext-abandon-queue') then
    perform cron.unschedule('cleanup-slicktext-abandon-queue');
  end if;
end $$;

drop function if exists public.cleanup_slicktext_abandon_queue();
drop table public.slicktext_abandon_queue;

commit;

notify pgrst, 'reload schema';

-- Conferência (últimas 24h):
-- select lead_type, status, left(last_error, 60) as motivo, count(*)
-- from public.slicktext_sync_queue
-- where created_at > now() - interval '1 day'
-- group by 1, 2, 3 order by 4 desc;
