-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Limpeza automática da zapier_webhook_queue (criada em
-- 20260917_create_zapier_webhook_queue.sql), via pg_cron rodando dentro do
-- próprio Postgres -- sem depender do worker ARQ pra isso.
--
-- Retenção:
--   - status='sent'   -> apaga depois de 7 dias
--   - status='failed' -> apaga depois de 30 dias (dá tempo de investigar
--     falha recorrente com o Zapier antes de perder o registro)
--
-- Se der erro de permissão no `create extension`, habilite pg_cron pelo
-- Dashboard do Supabase em Database > Extensions, e rode o resto do
-- arquivo depois.

create extension if not exists pg_cron with schema extensions;

create or replace function public.cleanup_zapier_webhook_queue()
returns void
language sql
as $$
  delete from public.zapier_webhook_queue
  where (status = 'sent' and sent_at < now() - interval '7 days')
     or (status = 'failed' and updated_at < now() - interval '30 days');
$$;

-- Idempotente: reagendar não duplica o job, só substitui o existente.
do $$
begin
  if exists (select 1 from cron.job where jobname = 'cleanup-zapier-webhook-queue') then
    perform cron.unschedule('cleanup-zapier-webhook-queue');
  end if;
end $$;

select cron.schedule(
  'cleanup-zapier-webhook-queue',
  '0 3 * * *', -- 03:00 UTC, todo dia
  $$ select public.cleanup_zapier_webhook_queue(); $$
);
