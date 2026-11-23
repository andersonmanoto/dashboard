-- Rode isso no SQL editor do Supabase (depois do deploy do código).
--
-- Abandonos com telefone de Porto Rico (+1 787/939) ou do México (+52) ficaram
-- como "falha em 1/1 conta(s): ...: FAILED": o filtro de país olhava só o país
-- do checkout, e o abandono não reconhecia a recusa de região da SlickText.
-- Não é falha da conta: reclassifica como regiao_nao_suportada (skipped,
-- igual ao que o código grava agora).

update public.slicktext_sync_queue
set status = 'skipped',
    last_error = 'regiao_nao_suportada'
where lead_type = 'abandon'
  and last_error like 'falha em %'
  and (
    regexp_replace(coalesce(phone, payload ->> 'phone', ''), '\D', '', 'g') ~ '^1?(787|939)\d{7}$'
    or coalesce(phone, payload ->> 'phone', '') like '+52%'
  );

-- Conferência: deve sobrar só o 251-461-8920 (Alabama, investigar no log)
-- select phone, last_error from public.slicktext_sync_queue where last_error like 'falha em %';
