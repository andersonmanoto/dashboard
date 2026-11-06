-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard
-- DEPOIS do deploy do worker e da API que gravam network_id.
--
-- Preenche os abandonos gravados pela API antiga durante o deploy (o cron só
-- envia abandono com network_id) e apaga a coluna de texto. Se sobrar abandono
-- sem rede, o check abaixo falha e nada é alterado.

begin;

update public.slicktext_sync_queue q
set network_id = n.id
from public.networks n
where q.network_id is null
  and n.name = q.network;

alter table public.slicktext_sync_queue
  add constraint slicktext_sync_queue_abandon_network_check
    check (lead_type <> 'abandon' or network_id is not null);

alter table public.slicktext_sync_queue drop column network;

commit;

notify pgrst, 'reload schema';
