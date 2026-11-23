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

-- +1 251-461-8920 (Alabama): mandou STOP na conta 09 às 00:56 UTC de 20/11
-- (opt_in_status = unsubscribed na SlickText); os abandonos seguintes
-- tentaram reinscrever e falharam. O código agora grava contato_descadastrado.
update public.slicktext_sync_queue
set status = 'skipped',
    last_error = 'contato_descadastrado'
where lead_type = 'abandon'
  and last_error like 'falha em %'
  and regexp_replace(coalesce(phone, payload ->> 'phone', ''), '\D', '', 'g') in ('2514618920', '12514618920');

-- Conferência: não deve sobrar nada
-- select phone, last_error from public.slicktext_sync_queue where last_error like 'falha em %';
