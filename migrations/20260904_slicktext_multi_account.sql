-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Introduz suporte a múltiplas contas SlickText. Hoje só existia 1 conta
-- global (via SLICKTEXT_API_KEY/SLICKTEXT_BRAND_ID no .env) e 2 colunas fixas
-- em products (slicktext_approved_list_id / slicktext_abandoned_list_id).
-- Isso substitui essas colunas por uma tabela de mapeamento produto -> conta.
--
-- Um produto pode estar mapeado em MAIS DE UMA conta ao mesmo tempo (ex: a
-- conta original -- "SMS TIGER CONTIGENCIA" -- funciona como contingência e
-- deve continuar recebendo a mesma informação em paralelo à conta nova).
-- Por isso a chave primária é composta (product_id, slick_account_id): o
-- código busca TODAS as linhas de um produto e envia o mesmo payload pra
-- cada conta, cada uma com seu próprio list_id.
--
-- Segredos (api_key) continuam só no .env, com a convenção
-- SLICKTEXT_API_KEY_<BRAND_ID> (ex: brand_id '35016' -> SLICKTEXT_API_KEY_35016).
-- O sufixo é o brand_id, não o name da conta -- name é só um rótulo humano
-- (ex: "SMS TIGER CONTIGENCIA") e pode ser renomeado livremente sem quebrar
-- a resolução de credenciais no código. O brand_id em si não é sensível
-- (é só parte do path da API) e fica em slicktext_accounts.
--
-- As colunas antigas em products (slicktext_approved_list_id /
-- slicktext_abandoned_list_id) NÃO são dropadas aqui de propósito -- ficam
-- como deprecated até o cutover no código ser validado em produção. Dropar
-- em uma migration separada depois.
--
-- Dropa slicktext_accounts/slicktext_product_lists antes de recriar: seguro
-- porque são tabelas novas, sem nenhum outro código/dado dependendo delas.
drop table if exists public.slicktext_product_lists cascade;
drop table if exists public.slicktext_accounts cascade;

create table if not exists public.slicktext_accounts (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    brand_id text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.slicktext_product_lists (
    product_id uuid not null references public.products(id) on delete cascade,
    slick_account_id uuid not null references public.slicktext_accounts(id),
    approved_list_id text,
    abandoned_list_id text,
    updated_at timestamptz not null default now(),
    primary key (product_id, slick_account_id)
);

create index if not exists idx_slicktext_product_lists_slick_account_id
    on public.slicktext_product_lists (slick_account_id);

create or replace function public.set_slicktext_product_lists_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists trg_slicktext_product_lists_updated_at on public.slicktext_product_lists;
create trigger trg_slicktext_product_lists_updated_at
    before update on public.slicktext_product_lists
    for each row
    execute function public.set_slicktext_product_lists_updated_at();

-- Conta original ("contingência"), a mesma já usada via
-- SLICKTEXT_API_KEY/SLICKTEXT_BRAND_ID no .env. O código continua lendo a
-- api_key dessa conta a partir dessas variáveis (sem sufixo) quando
-- slicktext_accounts.brand_id = SLICKTEXT_BRAND_ID -- o brand_id que já
-- está no .env atual é '34641'.
insert into public.slicktext_accounts (name, brand_id)
select 'SMS TIGER CONTIGENCIA', '34641'
where not exists (select 1 from public.slicktext_accounts where brand_id = '34641');

-- Backfill: todo produto que já tinha list_id mapeado migra pra conta
-- original/contingência, preservando os list_ids que já existiam.
insert into public.slicktext_product_lists (product_id, slick_account_id, approved_list_id, abandoned_list_id)
select p.id, a.id, p.slicktext_approved_list_id::text, p.slicktext_abandoned_list_id::text
from public.products p
cross join (select id from public.slicktext_accounts where brand_id = '34641') a
where p.slicktext_approved_list_id is not null
   or p.slicktext_abandoned_list_id is not null
on conflict (product_id, slick_account_id) do nothing;

-- Nova conta: Adspower "SMS CONTA 08 - Velupet" (brand_id 35016). A api_key
-- vive na env var SLICKTEXT_API_KEY_35016 (ver .env / .env.example).
insert into public.slicktext_accounts (name, brand_id)
select 'SMS CONTA 08 - Velupet', '35016'
where not exists (select 1 from public.slicktext_accounts where brand_id = '35016');

-- Estes 5 produtos passam a enviar TAMBÉM pra conta Velupet, além da conta
-- original/contingência (mesma informação, list_id próprio de cada conta --
-- não sobrescreve a linha da contingência, é uma linha adicional).
insert into public.slicktext_product_lists (product_id, slick_account_id, approved_list_id, abandoned_list_id)
select p.id, a.id, v.approved_list_id, v.abandoned_list_id
from public.products p
join (values
    ('BreathEaseX',   '162334', '162335'),
    ('NervoLyn',      '162336', '162337'),
    ('NailsCleanPro', '162338', '162340'),
    ('AudiLeaf',      '162341', '162342'),
    ('Prostafense',   '162343', '162344')
) as v(name, approved_list_id, abandoned_list_id) on v.name = p.name
cross join (select id from public.slicktext_accounts where brand_id = '35016') a
on conflict (product_id, slick_account_id) do update
    set approved_list_id = excluded.approved_list_id,
        abandoned_list_id = excluded.abandoned_list_id,
        updated_at = now();
