-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Contadores por input em slicktext_inputs, pedidos pelo front:
--   qty_synced  linhas da fila deste input com status 'synced' (entraram na lista)
--   qty_failed  linhas da fila deste input com status 'failed'
--
-- Quem mantém é um trigger na slicktext_input_queue, não o backend: o número
-- fica certo qualquer que seja o caminho que mexe na linha (cron, ajuste
-- manual). Só INSERT e UPDATE de status: o contrato não apaga linha da fila.
--
-- Roda numa transação só e trava a fila contra escrita (leitura continua)
-- enquanto cria o trigger e faz o backfill, pra nenhum update escapar do
-- trigger nem ser contado duas vezes.

begin;

lock table public.slicktext_input_queue in share mode;

alter table public.slicktext_inputs
  add column if not exists qty_synced int not null default 0,
  add column if not exists qty_failed int not null default 0;

comment on column public.slicktext_inputs.qty_synced is
  'Linhas da slicktext_input_queue deste input com status synced (mantido por trigger).';
comment on column public.slicktext_inputs.qty_failed is
  'Linhas da slicktext_input_queue deste input com status failed (mantido por trigger).';

create or replace function public.sms_input_queue_count_status()
returns trigger
language plpgsql
set search_path = public
as $$
declare
  d_synced int := coalesce(new.status = 'synced', false)::int;
  d_failed int := coalesce(new.status = 'failed', false)::int;
begin
  if tg_op = 'UPDATE' then
    d_synced := d_synced - coalesce(old.status = 'synced', false)::int;
    d_failed := d_failed - coalesce(old.status = 'failed', false)::int;
  end if;

  if d_synced <> 0 or d_failed <> 0 then
    update public.slicktext_inputs
    set qty_synced = qty_synced + d_synced,
        qty_failed = qty_failed + d_failed
    where id = new.input_id;
  end if;
  return null;
end;
$$;

drop trigger if exists trg_sms_input_queue_count_insert on public.slicktext_input_queue;
create trigger trg_sms_input_queue_count_insert
  after insert on public.slicktext_input_queue
  for each row
  when (new.status in ('synced', 'failed'))
  execute function public.sms_input_queue_count_status();

drop trigger if exists trg_sms_input_queue_count_update on public.slicktext_input_queue;
create trigger trg_sms_input_queue_count_update
  after update of status on public.slicktext_input_queue
  for each row
  when (old.status is distinct from new.status)
  execute function public.sms_input_queue_count_status();

-- backfill dos inputs que já existem
update public.slicktext_inputs i
set qty_synced = c.synced,
    qty_failed = c.failed
from (
  select input_id,
         count(*) filter (where status = 'synced') as synced,
         count(*) filter (where status = 'failed') as failed
  from public.slicktext_input_queue
  group by input_id
) c
where c.input_id = i.id;

commit;
