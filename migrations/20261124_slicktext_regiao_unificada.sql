-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Unifica "fora de EUA/Canadá" num código só na slicktext_sync_queue:
--
--   país fora do escopo: XX  (abandono, país do checkout)  -> regiao_nao_suportada
--   telefone_invalido de venda cujo país de entrega é de
--   fora de EUA/Canadá (número local AU/GB/NZ que não fecha
--   como EUA)                                              -> regiao_nao_suportada
--
-- O código já existia pra quando o país do NÚMERO é de fora (Porto Rico...).
-- O país em si vai pro log, não pro código (app/services/slicktext_errors.py).
-- Idempotente.

begin;

-- 1. Abandono: país do checkout fora de EUA/Canadá
update public.slicktext_sync_queue
set last_error = 'regiao_nao_suportada'
where last_error like 'país fora do escopo:%';

-- 2. Venda: telefone "inválido" de comprador de fora de EUA/Canadá
update public.slicktext_sync_queue q
set last_error = 'regiao_nao_suportada'
from public.events e
where e.id = q.event_id
  and q.lead_type = 'neworder'
  and q.last_error = 'telefone_invalido'
  and nullif(btrim(e.shipping_details ->> 'country'), '') is not null
  and lower(btrim(e.shipping_details ->> 'country')) not in (
    'us', 'usa', 'united states', 'united states of america', 'ca', 'canada'
  );

commit;

-- Conferência:
-- select lead_type, last_error, count(*)
-- from public.slicktext_sync_queue
-- where last_error in ('regiao_nao_suportada', 'telefone_invalido')
--    or last_error like 'país fora do escopo:%'
-- group by 1, 2 order by 3 desc;
