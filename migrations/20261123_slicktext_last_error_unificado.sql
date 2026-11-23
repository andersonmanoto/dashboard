-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Unifica o last_error da slicktext_sync_queue: venda e abandono passam a
-- gravar o mesmo código pra mesma checagem (app/services/slicktext_errors.py).
-- Este backfill converte as linhas antigas que ainda estão na fila.
--
--   telefone vazio ou inválido / telefone_invalido
--       -> telefone_vazio     (lead chegou sem telefone)
--       -> telefone_invalido  (tem telefone, mas não é número válido)
--   telefone reprovado pela AbstractAPI       -> rejeitado_abstract
--   lista de abandono não mapeada: <produto>  -> lista_nao_mapeada
--   lista_nao_encontrada (venda)              -> lista_nao_mapeada
--   nenhuma conta active: <produto>           -> sem_conta_active
--   AbstractAPI indisponível                  -> abstract_api_error
--
-- Telefone: abandono olha a coluna phone / payload->>'phone'; venda olha
-- events.customer_phone.
--
-- A slicktext_input_queue NÃO muda: lá lista_nao_encontrada é um 404 da
-- SlickText (lista não existe mais), outra coisa.

begin;

-- 1. Telefone (abandono)
update public.slicktext_sync_queue q
set last_error = case
      when coalesce(nullif(btrim(q.phone), ''), nullif(btrim(q.payload ->> 'phone'), '')) is null
        then 'telefone_vazio'
      else 'telefone_invalido'
    end
where q.lead_type = 'abandon'
  and q.last_error in ('telefone vazio ou inválido', 'telefone_invalido');

-- 2. Telefone (venda)
update public.slicktext_sync_queue q
set last_error = case
      when nullif(btrim(e.customer_phone), '') is null then 'telefone_vazio'
      else 'telefone_invalido'
    end
from public.events e
where e.id = q.event_id
  and q.lead_type = 'neworder'
  and q.last_error = 'telefone_invalido';

-- 3. Demais códigos
update public.slicktext_sync_queue
set last_error = 'rejeitado_abstract'
where last_error = 'telefone reprovado pela AbstractAPI';

update public.slicktext_sync_queue
set last_error = 'lista_nao_mapeada'
where last_error = 'lista_nao_encontrada'
   or last_error like 'lista de abandono não mapeada:%';

update public.slicktext_sync_queue
set last_error = 'sem_conta_active'
where last_error like 'nenhuma conta active:%';

update public.slicktext_sync_queue
set last_error = 'abstract_api_error'
where last_error = 'AbstractAPI indisponível';

commit;

-- Conferência:
-- select lead_type, status, last_error, count(*)
-- from public.slicktext_sync_queue
-- where status in ('failed', 'skipped', 'retry')
-- group by 1, 2, 3 order by 4 desc;
