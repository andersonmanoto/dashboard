-- Mapeamento produto -> listas do AWeber pra BuyGoods. GERADO em 2026-11-05
-- cruzando products.name com o nome das listas ("X" / "X Abandoned"). REVISE
-- antes de rodar. Rodar DEPOIS de 20261105_aweber_product_lists_networks.sql.
--
-- Nada aqui liga a BuyGoods: enabled_networks fica sem 'BuyGoods'. Pra ligar um
-- produto, rode o update do fim no MESMO momento em que desliga o autoresponder
-- daquele produto no painel da BuyGoods.
--
-- Coluna 'COI' = contatos não confirmados na lista em 2026-11-05. Lista com COI
-- ligado faz o lead da API também ficar 'unconfirmed': desligue antes de ligar.

insert into public.aweber_product_lists (product_id, neworder_list_id, abandon_list_id, enabled_networks)
values
  ('f0494709-01fb-4d0e-8e8c-3e200f25b13c', '6977002', '6977004', '{}'),  -- SodaHorsePeak (2705 vendas BG out-nov): SodaHorsePeak / SodaHorsePeak (Abandoned)
  ('8b6cb6d4-2f45-43bf-b569-6286a8e47a91', '6974794', '6974795', '{}'),  -- NailsCleanPro (988 vendas BG out-nov): NailsCleanPro / NailsCleanPro Abandoned | COI: 666/509
  ('66da4676-c1fc-411e-87a0-c6c0dca99965', '6955262', '6955263', '{}'),  -- MaroBrain (968 vendas BG out-nov): Marobrain / Marobrain Abandoned
  ('b8c0aadc-f30c-4519-ba98-e940c452701a', '6951158', '6951619', '{}'),  -- NervoLyn (528 vendas BG out-nov): Nervolyn / Nervolyn Abandoned
  ('dc1ceea6-405b-482d-96af-777c19cc3577', '6959382', '6959384', '{}'),  -- BreathEaseX (396 vendas BG out-nov): BreatheaseX / BreatheaseX Abandoned
  ('db2a0e57-d54e-41c6-8af3-823c00c21ad5', '6955256', '6955261', '{}'),  -- AudiLeaf (376 vendas BG out-nov): AudiLeaf / AudiLeaf Abandoned
  ('ebdc4e43-463e-402a-bd3a-b7b30e234f09', '6967211', '6967213', '{}'),  -- PressureCalmX (374 vendas BG out-nov): PressureCalmX / PressureCalmX Abandoned | COI: 296/246
  ('84150047-6b73-478b-b0e2-bf99a266cc41', '6980340', '6980341', '{}'),  -- FloraNew (325 vendas BG out-nov): FloraNew / FloraNew (Abandoned) | COI: 95/82
  ('8d4c373a-44c1-4ef9-9835-452e87688ea7', '6910165', '6912034', '{}'),  -- Prostafense (263 vendas BG out-nov): Prostafense / Prostafense Abandoned
  ('806cd4f6-45d1-4d5f-a56b-5ed36c2663b3', '6910174', '6912030', '{}'),  -- VisiumPro (167 vendas BG out-nov): VisiumPro / Visium Pro Abandoned
  ('6edc9db7-fd74-43e0-819a-3c851c6d7bc9', '6977849', '6977850', '{}'),  -- BakingSlim (153 vendas BG out-nov): BakingSlim / BakingSlim Abandoned | COI: 83/82
  ('a1b4ccc2-dd7e-4080-a00e-911f10123ffc', '6962289', '6962290', '{}'),  -- IronPulseX (107 vendas BG out-nov): IronPulseX / IronPulseX Abandoned | COI: 5/6
  ('ecfb897e-2cb8-4d52-ae21-02ede303193b', '6962283', '6962284', '{}'),  -- AlphaErec (99 vendas BG out-nov): AlphaErec / AlphaErec Abandoned | COI: 60/97
  ('702fbd9f-efea-4841-9955-8b38126da7a6', '6978575', '6978576', '{}'),  -- GoldenSkinPro (76 vendas BG out-nov): GoldenSkinPro (NewOrder) / GoldenSkinPro (Abandoned) | COI: 6/6
  ('d3749ad8-4653-4a36-9854-7604fd71391a', '6951168', '6951626', '{}'),  -- GlucoRecover (64 vendas BG out-nov): GlucoRecover / GlucoRecover Abandoned
  ('c438ebd4-0dd8-4a6a-94f1-f173c79270b3', '6980184', '6980185', '{}'),  -- MatchaGlide (36 vendas BG out-nov): MatchaGlide / MatchaGlide (Abandoned) | COI: 42/72
  ('e52d831d-abd6-4753-bb4e-e5fd99c28bef', '6962291', '6962293', '{}'),  -- VigorTitan (33 vendas BG out-nov): VigorTitan / VigorTitan Abandoned | COI: 27/20
  ('008a4e25-e1ec-4270-807e-a16e73ce490f', '6910168', '6912038', '{}'),  -- VigorLong (29 vendas BG out-nov): Vigor Long / VigorLong Abandoned
  ('13b8343a-b077-4e3c-954c-999525bcde4a', '6963254', '6963255', '{}'),  -- BoosterXT (27 vendas BG out-nov): BoosterXT / BoosterXT Abandoned
  ('8d85e1c0-c56e-4dfb-8cbb-5e2c95026200', '6966231', '6966232', '{}'),  -- JointReliveX (10 vendas BG out-nov): JointReliveX / JointReliveX Abandoned | COI: 8/14
  ('b0acd2a2-bfa8-41ad-bbc5-1c839e8f9d54', '6962285', '6962287', '{}'),  -- BoostSteelX (5 vendas BG out-nov): BoostSteelX / BoostSteelX Abandoned
  ('2bf4f3b9-a5bb-4d8c-adcd-50f656c53e16', '6982267', '6982269', '{}'),  -- LungNew (5 vendas BG out-nov): LungNew / LungNew Abandoned
  ('33b98ccb-8972-4d51-b554-e5c6c5afafe1', '6965082', '6965083', '{}'),  -- ProstaLiss (4 vendas BG out-nov): ProstaLiss / ProstaLiss Abandoned | COI: 0/5
  ('e7cd0198-604a-40e3-8c7c-b06701034b30', '6980041', '6980183', '{}'),  -- DermaGummies (4 vendas BG out-nov): DermaGummies / DermaGummies (Abandoned) | COI: 6/9
  ('d30ca6b3-fd85-4327-a8ba-8a5798bfc7b4', '6965046', '6965048', '{}'),  -- GlycoMelt (3 vendas BG out-nov): GlycoMelt / GlycoMelt Abandoned | COI: 0/2
  ('489c17b4-ba45-407e-95c7-636432729f66', '6966376', '6966377', '{}'),  -- EyeVitalis (2 vendas BG out-nov): EyeVitalis / EyeVitalis Abandoned
  ('5aa61da2-588b-4e18-b741-e44adadf178e', '6983704', '6983705', '{}'),  -- BullSodaRise (2 vendas BG out-nov): BullSodaRise / BullSodaRise Abandoned | COI: 0/1
  ('ad7a4007-3041-417c-aec5-6239b285ac2f', '6966537', '6966538', '{}')  -- SkinRenewPro (1 vendas BG out-nov): SkinRenewPro / SkinRenewPro Abandoned
on conflict (product_id) do nothing;  -- SodaHorsePeak já existe (PagAmerican): mantém

-- Sem par de listas no AWeber (crie as listas ou mapeie à mão):
--   LipoPeak (2 vendas): vendas=None | abandono=None
--   LipoBliss (1 vendas): vendas=None | abandono=None

-- Ligar a BuyGoods num produto:
--   update public.aweber_product_lists
--   set enabled_networks = array_append(enabled_networks, 'BuyGoods'), updated_at = now()
--   where product_id = '<products.id>' and not ('BuyGoods' = any(enabled_networks));
