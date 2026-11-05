-- Catálogo produto -> listas do AWeber (vendas / abandono), GERADO em 2026-11-05
-- cruzando products.name com o nome das listas ("X" / "X Abandoned"). REVISE.
--
-- Tudo comentado de propósito: rodar este arquivo NÃO faz nada. Uma linha em
-- aweber_product_lists liga o produto pra TODAS as redes (PagAmerican e
-- BuyGoods), vendas e abandono. Pra migrar um produto da BuyGoods, copie a linha
-- dele e rode no MESMO momento em que desliga os 2 autoresponders do produto no
-- painel da BuyGoods.
--
-- SodaHorsePeak não está aqui: já tem linha (PagAmerican) e, com o deploy deste
-- código, passa a mandar os leads da BuyGoods também.
--
-- 'COI' = contatos não confirmados na lista (vendas/abandono) em 2026-11-05.
-- Lista com COI ligado deixa o lead da API 'unconfirmed': desligue antes.

-- NailsCleanPro (988 vendas BG out-nov): NailsCleanPro / NailsCleanPro Abandoned | COI: 666/509
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('8b6cb6d4-2f45-43bf-b569-6286a8e47a91', '6974794', '6974795');

-- MaroBrain (968 vendas BG out-nov): Marobrain / Marobrain Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('66da4676-c1fc-411e-87a0-c6c0dca99965', '6955262', '6955263');

-- NervoLyn (528 vendas BG out-nov): Nervolyn / Nervolyn Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('b8c0aadc-f30c-4519-ba98-e940c452701a', '6951158', '6951619');

-- BreathEaseX (396 vendas BG out-nov): BreatheaseX / BreatheaseX Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('dc1ceea6-405b-482d-96af-777c19cc3577', '6959382', '6959384');

-- AudiLeaf (376 vendas BG out-nov): AudiLeaf / AudiLeaf Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('db2a0e57-d54e-41c6-8af3-823c00c21ad5', '6955256', '6955261');

-- PressureCalmX (374 vendas BG out-nov): PressureCalmX / PressureCalmX Abandoned | COI: 296/246
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('ebdc4e43-463e-402a-bd3a-b7b30e234f09', '6967211', '6967213');

-- FloraNew (325 vendas BG out-nov): FloraNew / FloraNew (Abandoned) | COI: 95/82
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('84150047-6b73-478b-b0e2-bf99a266cc41', '6980340', '6980341');

-- Prostafense (263 vendas BG out-nov): Prostafense / Prostafense Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('8d4c373a-44c1-4ef9-9835-452e87688ea7', '6910165', '6912034');

-- VisiumPro (167 vendas BG out-nov): VisiumPro / Visium Pro Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('806cd4f6-45d1-4d5f-a56b-5ed36c2663b3', '6910174', '6912030');

-- BakingSlim (153 vendas BG out-nov): BakingSlim / BakingSlim Abandoned | COI: 83/82
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('6edc9db7-fd74-43e0-819a-3c851c6d7bc9', '6977849', '6977850');

-- IronPulseX (107 vendas BG out-nov): IronPulseX / IronPulseX Abandoned | COI: 5/6
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('a1b4ccc2-dd7e-4080-a00e-911f10123ffc', '6962289', '6962290');

-- AlphaErec (99 vendas BG out-nov): AlphaErec / AlphaErec Abandoned | COI: 60/97
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('ecfb897e-2cb8-4d52-ae21-02ede303193b', '6962283', '6962284');

-- GoldenSkinPro (76 vendas BG out-nov): GoldenSkinPro (NewOrder) / GoldenSkinPro (Abandoned) | COI: 6/6
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('702fbd9f-efea-4841-9955-8b38126da7a6', '6978575', '6978576');

-- GlucoRecover (64 vendas BG out-nov): GlucoRecover / GlucoRecover Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('d3749ad8-4653-4a36-9854-7604fd71391a', '6951168', '6951626');

-- MatchaGlide (36 vendas BG out-nov): MatchaGlide / MatchaGlide (Abandoned) | COI: 42/72
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('c438ebd4-0dd8-4a6a-94f1-f173c79270b3', '6980184', '6980185');

-- VigorTitan (33 vendas BG out-nov): VigorTitan / VigorTitan Abandoned | COI: 27/20
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('e52d831d-abd6-4753-bb4e-e5fd99c28bef', '6962291', '6962293');

-- VigorLong (29 vendas BG out-nov): Vigor Long / VigorLong Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('008a4e25-e1ec-4270-807e-a16e73ce490f', '6910168', '6912038');

-- BoosterXT (27 vendas BG out-nov): BoosterXT / BoosterXT Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('13b8343a-b077-4e3c-954c-999525bcde4a', '6963254', '6963255');

-- JointReliveX (10 vendas BG out-nov): JointReliveX / JointReliveX Abandoned | COI: 8/14
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('8d85e1c0-c56e-4dfb-8cbb-5e2c95026200', '6966231', '6966232');

-- BoostSteelX (5 vendas BG out-nov): BoostSteelX / BoostSteelX Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('b0acd2a2-bfa8-41ad-bbc5-1c839e8f9d54', '6962285', '6962287');

-- LungNew (5 vendas BG out-nov): LungNew / LungNew Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('2bf4f3b9-a5bb-4d8c-adcd-50f656c53e16', '6982267', '6982269');

-- ProstaLiss (4 vendas BG out-nov): ProstaLiss / ProstaLiss Abandoned | COI: 0/5
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('33b98ccb-8972-4d51-b554-e5c6c5afafe1', '6965082', '6965083');

-- DermaGummies (4 vendas BG out-nov): DermaGummies / DermaGummies (Abandoned) | COI: 6/9
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('e7cd0198-604a-40e3-8c7c-b06701034b30', '6980041', '6980183');

-- GlycoMelt (3 vendas BG out-nov): GlycoMelt / GlycoMelt Abandoned | COI: 0/2
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('d30ca6b3-fd85-4327-a8ba-8a5798bfc7b4', '6965046', '6965048');

-- EyeVitalis (2 vendas BG out-nov): EyeVitalis / EyeVitalis Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('489c17b4-ba45-407e-95c7-636432729f66', '6966376', '6966377');

-- BullSodaRise (2 vendas BG out-nov): BullSodaRise / BullSodaRise Abandoned | COI: 0/1
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('5aa61da2-588b-4e18-b741-e44adadf178e', '6983704', '6983705');

-- SkinRenewPro (1 vendas BG out-nov): SkinRenewPro / SkinRenewPro Abandoned
-- insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id) values ('ad7a4007-3041-417c-aec5-6239b285ac2f', '6966537', '6966538');

-- Sem par de listas no AWeber (crie as listas ou mapeie à mão):
--   LipoPeak (2 vendas): vendas=None | abandono=None
--   LipoBliss (1 vendas): vendas=None | abandono=None

-- Desligar um produto (volta pro autoresponder da BuyGoods):
--   delete from public.aweber_product_lists where product_id = '<products.id>';
