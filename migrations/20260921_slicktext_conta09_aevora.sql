-- Rode isso no SQL editor do Supabase (ou via psql) do projeto do dashboard.
--
-- Nova conta SlickText: "SMS CONTA 09 - aevora" (brand_id 35113). A conta já
-- existe em public.slicktext_accounts (id 653ea24d-5257-42a1-bac8-5dfe06a4ebcd,
-- criada em 2026-09-11) -- o insert abaixo é só pra idempotência caso este
-- script rode num ambiente onde ela ainda não exista. A api_key correspondente
-- vive em SLICKTEXT_API_KEY_35113 no .env (ver .env.example).
--
-- Mapeia 14 produtos pra essa conta (aditivo -- não mexe nas linhas que esses
-- produtos já tenham em outras contas, ex: SMS TIGER CONTIGENCIA).
--
-- Os nomes abaixo batem com public.products.name; alguns diferem do texto
-- original recebido (sem espaço/case) -- conferido por ilike antes de montar
-- este script:
--   "Marobrain"      -> "MaroBrain"
--   "Visium Pro"     -> "VisiumPro"
--   "Gluco Recover"  -> "GlucoRecover"
--   "Vigorlong"      -> "VigorLong"
--   "Booster XT"     -> "BoosterXT" (existe também "BoosterXT German",
--                        confirmado com o usuário que NÃO é esse)

insert into public.slicktext_accounts (name, brand_id)
select 'SMS CONTA 09 - aevora', '35113'
where not exists (select 1 from public.slicktext_accounts where brand_id = '35113');

insert into public.slicktext_product_lists (product_id, slick_account_id, approved_list_id, abandoned_list_id)
select p.id, a.id, v.approved_list_id, v.abandoned_list_id
from public.products p
join (values
    ('AlphaErec',      '173008', '173009'),
    ('FloraNew',       '173010', '173011'),
    ('MaroBrain',      '173012', '173013'),
    ('BakingSlim',     '173014', '173015'),
    ('VisiumPro',      '173017', '173018'),
    ('GlucoRecover',   '173019', '173020'),
    ('SodaHorsePeak',  '173021', '173022'),
    ('PressureCalmX',  '173023', '173024'),
    ('EyeVitalis',     '173025', '173026'),
    ('VigorLong',      '173027', '173029'),
    ('BoosterXT',      '173030', '173031'),
    ('IronPulseX',     '173032', '173033'),
    ('LipoBliss',      '173034', '173035'),
    ('SkinRenewPro',   '173036', '173037')
) as v(name, approved_list_id, abandoned_list_id) on v.name = p.name
cross join (select id from public.slicktext_accounts where brand_id = '35113') a
on conflict (product_id, slick_account_id) do update
    set approved_list_id = excluded.approved_list_id,
        abandoned_list_id = excluded.abandoned_list_id,
        updated_at = now();
