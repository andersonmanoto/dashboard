-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
-- Duas colunas novas em active_funnel_history (ver migrations/20260812_funnel_sync.sql
-- pro contexto da tabela).
--
--   status: 'success' ou 'error'. Reflete se TODOS os arquivos HTML tocados
--     nessa sincronização passaram na verificação pós-escrita (files_changed[].ok
--     em funnel_sync_service.py) — não é sobre o sync como um todo, que só
--     chega a gravar essa linha quando não lançou exceção (ver _run_sync).
--     Uma falha aqui significa "o número do funil mudou, mas pelo menos um
--     arquivo pode não ter sido atualizado corretamente" — vale conferir os
--     backups/diff no log.
--
--   link_changes: jsonb dinâmico { "<domain>/<subdir>": { "switchLink-N": "nova_url", ... }, ... }.
--     Uma chave por diretório de página tocado (dtc, discover, discover/ot, discover/OT, etc. —
--     não é uma lista fixa de subdiretórios, é derivado do path real no servidor),
--     contendo os switchLink ids trocados nesse diretório e a nova url de cada um.

alter table active_funnel_history
  add column if not exists status text,
  add column if not exists link_changes jsonb;

alter table active_funnel_history
  drop constraint if exists active_funnel_history_status_check;

alter table active_funnel_history
  add constraint active_funnel_history_status_check
  check (status is null or status in ('success', 'error'));
