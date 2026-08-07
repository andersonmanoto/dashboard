import asyncio
import io
from pathlib import Path
from datetime import datetime
from typing import Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML
import resend
from loguru import logger

from app.config import Settings
from app.repositories.database import DatabaseRepository
from app.services import affiliate_report_aggregator as aggregator
from app.services.affiliate_xlsx_builder import build_affiliate_report_workbook
from supabase import create_async_client


class ReportService:
    """
    Serviço dedicado à geração de PDFs e envio de e-mails.
    Lógica de auditoria baseada na taxa dinâmica por Network.
    """

    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        self.settings = settings
        self.db = db_repo
        self.template_dir = Path(__file__).parent.parent.parent / "templates"

        resend.api_key = self.settings.resend_api_key

        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def _fetch_grouped_data(self, filters: dict) -> dict:
        transactions = self.db.get_high_fee_transactions(filters)

        grouped = {}
        for tx in transactions:
            # 1. Captura a taxa dinâmica
            network_tax = tx.get("networks", {}).get("tax")
            agreed_tax = float(network_tax) if network_tax is not None else 0.07
            tx["agreed_tax"] = agreed_tax

            actual_rate = tx.get("merchant_commission_rate", 0)
            if actual_rate <= agreed_tax:
                continue

            # 2. Formatação da data da transação: MM-DD-YYYY
            raw_date = tx.get("event_date")
            formatted_date = "N/A"
            if raw_date:
                try:
                    date_str = str(raw_date).split()[0]
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    formatted_date = dt.strftime("%m-%d-%Y")
                except Exception as e:
                    logger.warning(f"Erro ao formatar data {raw_date}: {e}")
                    formatted_date = str(raw_date)

            tx["event_date"] = formatted_date

            # 3. Lógica de Agrupamento
            p_name = tx.get("products", {}).get("name") or "Unknown Product"
            acc_id = tx.get("account_id") or "N/A"
            key = f"{p_name} ({acc_id})"

            if key not in grouped:
                grouped[key] = []
            grouped[key].append(tx)

        return grouped

    def _generate_pdf_bytes(self, html_string: str) -> bytes:
        """Converte HTML para PDF usando WeasyPrint."""
        pdf_buffer = io.BytesIO()
        HTML(string=html_string, base_url=str(self.template_dir)).write_pdf(pdf_buffer)
        return pdf_buffer.getvalue()

    async def generate_and_send_report(
        self,
        user_email: str,
        filters: dict,
        template_name: str = "buygoods_fee_audit.html",
    ):
        """
        Orquestra a geração e envio. Um e-mail com múltiplos anexos (um por produto).
        """
        # Formatação das datas do período para MM-DD-YYYY
        try:
            p_start = datetime.strptime(
                filters["period"]["start_date"], "%Y-%m-%d"
            ).strftime("%m-%d-%Y")
            p_end = datetime.strptime(
                filters["period"]["end_date"], "%Y-%m-%d"
            ).strftime("%m-%d-%Y")
        except Exception:
            p_start = filters["period"]["start_date"]
            p_end = filters["period"]["end_date"]

        try:
            # 1. Busca e Agrupa dados
            grouped_data = await asyncio.to_thread(self._fetch_grouped_data, filters)

            # 2. CENÁRIO: NADA CONSTA (Feedback Positivo)
            if not grouped_data:
                logger.info(
                    f"Feedback positivo: Nenhuma taxa abusiva para {user_email}."
                )
                params = {
                    "from": self.settings.email_from,
                    "to": [user_email],
                    "subject": "BuyGoods Fee Audit Completed – No Overcharges Found",
                    "html": f"""
                    <p>Hello,</p>

                    <p>Your <strong>BuyGoods fee audit</strong> for the period 
                    <strong>{p_start}</strong> to <strong>{p_end}</strong> has been completed.</p>

                    <p>After reviewing the transactions for the selected products, 
                    <strong>no platform fees were identified above the agreed threshold</strong>.</p>

                    <p>This means that, for this period, all analyzed transactions appear to have 
                    been charged according to the expected fee structure.</p>

                    <p>No further action is required at this time.</p>

                    <p>If you would like us to review a different period or additional products, 
                    feel free to run a new audit at any time.</p>

                    <p>Best regards,<br>
                    <strong>Tiger Offers Team</strong></p>
                    """,
                }
                await asyncio.to_thread(resend.Emails.send, params)
                return True

            attachments = []

            # 3. Loop: Geração de um PDF por produto
            for group_name, items in grouped_data.items():
                prod_revenue = sum(item.get("sale_total", 0) for item in items)
                prod_fees = sum(item.get("merchant_commission", 0) for item in items)

                # Taxa dinâmica baseada na network do primeiro item do grupo
                agreed_rate = items[0].get("agreed_tax", 0.07)
                expected_fee = prod_revenue * agreed_rate
                overcharged_amount = max(0, prod_fees - expected_fee)

                context = {
                    "report_title": "Excessive Fee Analysis",
                    "period_start": p_start,
                    "period_end": p_end,
                    "total_transactions": len(items),
                    "total_affected_revenue": prod_revenue,
                    "total_fees_paid": prod_fees,
                    "overcharged_fees": overcharged_amount,
                    "agreed_rate_percent": agreed_rate * 100,
                    "grouped_transactions": {group_name: items},
                }

                template = self.jinja_env.get_template(template_name)
                html_string = template.render(**context)

                pdf_bytes = await asyncio.to_thread(
                    self._generate_pdf_bytes, html_string
                )

                clean_product_name = group_name.split(" (")[0].strip().replace(" ", "_")
                attachments.append(
                    {
                        "filename": f"AUDIT_{clean_product_name}.pdf",
                        "content": list(pdf_bytes),
                    }
                )

            # 4. Envio do E-mail Consolidado
            logger.info(
                f"Enviando e-mail com {len(attachments)} anexos para {user_email}"
            )

            email_params = {
                "from": self.settings.email_from,
                "to": [user_email],
                "subject": f"BuyGoods Fee Audit Report – {len(attachments)} Product(s) with Potential Overcharges",
                "html": f"""
                <p>Hello,</p>

                <p>Your <strong>BuyGoods fee audit</strong> for the period 
                <strong>{p_start}</strong> to <strong>{p_end}</strong> has been completed.</p>

                <p>During our analysis, we identified <strong>{len(attachments)} product(s)</strong> 
                where the platform fees charged appear to be higher than expected.</p>

                <p>For your convenience, we’ve attached a detailed report for each affected product. 
                These reports break down the transactions and highlight the fees that may require review.</p>

                <p>We recommend reviewing the attached files and contacting BuyGoods support if you wish 
                to dispute or request clarification on any of the charges.</p>

                <p>If you have any questions or need help interpreting the reports, feel free to reach out.</p>

                <p>Best regards,<br>
                <strong>Tiger Offers Team</strong></p>
                """,
                "attachments": attachments,
            }

            response = await asyncio.to_thread(resend.Emails.send, email_params)
            logger.success(
                f"Relatórios enviados com sucesso! Resend ID: {response.get('id')}"
            )
            return True

        except Exception as e:
            logger.exception(f"Erro fatal ao gerar relatório para {user_email}: {e}")
            raise e

    async def generate_and_send_dropoff_warning(
        self, target_emails: list[str], days_limit: int = 3
    ):
        """Busca afiliados em risco de churn, gera o PDF em lista corrida e envia por e-mail."""

        # 1. Busca no banco (já vem em ordem alfabética)
        records = await asyncio.to_thread(
            self.db.get_affiliates_without_recent_sales, days_limit
        )

        if not records:
            logger.info(
                "Nenhuma queda súbita detectada hoje. E-mail de warning não enviado."
            )
            return

        # Calcula os totais para o cabeçalho
        total_risk_volume = sum(float(row["volume_total_historico"]) for row in records)
        unique_products = len(set(row["produto"] for row in records))
        # Soma os reembolsos de todos os afiliados listados
        total_refund_30d = sum(
            float(row.get("reembolso_30_dias", 0)) for row in records
        )

        # 2. Renderiza o HTML (Passamos a lista plana 'records')
        context = {
            "days_limit": days_limit,
            "total_products": unique_products,
            "total_affiliates": len(records),
            "total_risk_volume": total_risk_volume,
            "total_refund_30d": total_refund_30d,
            "records": records,
        }

        template = self.jinja_env.get_template("affiliates_without_recent_sales.html")
        html_string = template.render(**context)

        # 3. Gera PDF
        pdf_bytes = await asyncio.to_thread(self._generate_pdf_bytes, html_string)

        # 4. Envia E-mail
        email_params = {
            "from": self.settings.email_from,
            "to": target_emails,
            "subject": f"Alerta: {len(records)} afiliados sem vendas entre 4 e 7 dias",
            "html": f"""
            <p>Olá, time de afiliados,</p>

            <p>O relatório <strong>Afiliados sem vendas</strong> está disponível.</p>

            <p>Identificamos <strong>{len(records)} afiliados ativos</strong> que não registraram vendas 
             <strong>entre 4 e 7 dias</strong>.</p>

            <p>O relatório completo segue em anexo, organizado em ordem alfabética.</p>

            <br>

            <p>Atenciosamente,<br>
            <strong>Tiger Offers Team</strong></p>
            """,
            "attachments": [
                {
                    "filename": f"afiliados_sem_vendas_{days_limit}d_{datetime.now().strftime('%Y_%m_%d')}.pdf",
                    "content": list(pdf_bytes),
                }
            ],
        }

        await asyncio.to_thread(resend.Emails.send, email_params)
        logger.success("Drop-off Warning (Lista Corrida) gerado e enviado com sucesso!")

    async def generate_and_send_chargeback_report(self, target_emails: list[str]):
        """Gera e envia o relatório de chargebacks por afiliado (30 dias)."""

        # 1. Busca os dados no banco
        records = await asyncio.to_thread(self.db.get_chargebacks_last_30_days)

        if not records:
            logger.info("Nenhum chargeback nos últimos 30 dias! E-mail não enviado.")
            return

        # 2. Calcula os totais para o cabeçalho
        total_loss = sum(float(row["total_amount_lost"]) for row in records)

        # 3. Renderiza o HTML
        context = {
            "total_affiliates": len(records),
            "total_loss": total_loss,
            "records": records,
        }

        template = self.jinja_env.get_template("chargeback_report.html")
        html_string = template.render(**context)

        # 4. Gera PDF
        pdf_bytes = await asyncio.to_thread(self._generate_pdf_bytes, html_string)

        # 5. Envia E-mail via Resend
        email_params = {
            "from": self.settings.email_from,
            "to": target_emails,
            "subject": "Alerta Financeiro: Relatório de Chargebacks (Últimos 30 Dias)",
            "html": f""" <p>Olá,</p> <p>O relatório de <strong>Chargebacks por Afiliado 
            — Últimos 30 Dias</strong> foi atualizado e encontra-se disponível para análise.
            </p> <p>No período analisado, foi identificado um impacto financeiro acumulado de 
            <strong>${total_loss:,.2f}</strong>, desconsiderando operações internas relacionadas à HelpGrid e Afiliado 0 (Tiger Offers).
            </p> <p>O relatório detalhado, contendo a distribuição por afiliado e produto, segue em anexo, 
            organizado do maior para o menor impacto financeiro.</p> <br> <p>Atenciosamente,
            <br> <strong>Tiger Offers Team</strong></p> """,
            "attachments": [
                {
                    "filename": f"chargebacks_30_dias_{datetime.now().strftime('%Y_%m_%d')}.pdf",
                    "content": list(pdf_bytes),
                }
            ],
        }

        await asyncio.to_thread(resend.Emails.send, email_params)
        logger.success("Relatório de Chargebacks gerado e enviado com sucesso!")

    async def generate_and_send_netrevenue_report(self, target_emails: list[str]):
        """Gera e envia o relatório de Net Revenue Negativo."""
        records = await asyncio.to_thread(self.db.get_negative_net_revenue_last_30_days)

        if not records:
            logger.info("Nenhum afiliado com Net Revenue negativo. Relatório ignorado.")
            return

        # Soma os prejuízos (os números vêm negativos do banco, então sum() já dá o total negativo)
        total_loss = sum(float(row["net_revenue"]) for row in records)

        context = {
            "total_affiliates": len(records),
            "total_loss": total_loss,
            "records": records,
        }

        template = self.jinja_env.get_template("negative_netrevenue_report.html")
        html_string = template.render(**context)

        pdf_bytes = await asyncio.to_thread(self._generate_pdf_bytes, html_string)

        email_params = {
            "from": self.settings.email_from,
            "to": target_emails,
            "subject": ("Alerta Financeiro: Net Revenue Negativo (Últimos 30 Dias)"),
            "html": f"""
            <p>Olá,</p>

            <p>
                O relatório consolidado de
                <strong>Net Revenue Negativo</strong>
                referente aos últimos 30 dias já está disponível.
            </p>

            <p>
                Durante o período analisado, foi identificado um impacto financeiro
                acumulado de
                <strong>${total_loss:,.2f}</strong>
                associado a afiliados com resultado líquido negativo.
            </p>

            <p>
                O relatório detalhado segue em anexo, contendo a consolidação por
                afiliado e produto.
            </p>

            <br>

            <p>
                Atenciosamente,<br>
                <strong>Tiger Offers Team</strong>
            </p>
            """,
            "attachments": [
                {
                    "filename": (
                        f"net_revenue_loss_{datetime.now().strftime('%Y_%m_%d')}.pdf"
                    ),
                    "content": list(pdf_bytes),
                }
            ],
        }

        await asyncio.to_thread(resend.Emails.send, email_params)
        logger.success("Relatório de Net Revenue gerado e enviado com sucesso!")

    def _resolve_owner_scope(self, requesting_user_id: Optional[str]) -> dict:
        """
        Decide se o pedido deve ser restrito aos afiliados do usuário solicitante.

        Regra espelha o `profiles.owner_filter` já usado no frontend: se o
        usuário tiver `owner_filter = true`, o relatório só pode conter
        afiliados cujo `owner` seja ele mesmo. Sem `requesting_user_id` (ou
        profile inexistente), mantém o comportamento atual sem restrição —
        preserva chamadas administrativas/crons que não enviam esse campo.
        """
        if not requesting_user_id:
            return {"restricted": False, "affiliate_ids": None, "owner_label": "Todos"}

        profile = self.db.get_profile(requesting_user_id)
        if not profile or not profile.get("owner_filter"):
            return {"restricted": False, "affiliate_ids": None, "owner_label": "Todos"}

        owner_ids = self.db.get_affiliate_ids_by_owner(requesting_user_id)
        return {
            "restricted": True,
            "affiliate_ids": owner_ids,
            "owner_label": f"Apenas afiliados de {profile.get('name') or requesting_user_id}",
        }

    def _fetch_affiliate_report_data(self, filters: dict) -> dict:
        """Busca (sincronamente) os 3 níveis de snapshot + lookups + % de COGS."""
        period = filters.get("period", {})
        start_date = str(period["start_date"])
        end_date = str(period["end_date"])
        network_id = filters.get("network_id")
        product_id = filters.get("product_id")

        owner_scope = self._resolve_owner_scope(filters.get("requesting_user_id"))
        owned_ids = owner_scope["affiliate_ids"]

        # Owner sem nenhum afiliado atribuído: não há o que buscar.
        if owner_scope["restricted"] and not owned_ids:
            return {
                "level1": [],
                "level2": [],
                "level3": [],
                "affiliates": {},
                "products": self.db.get_products_lookup(),
                "networks": self.db.get_networks_lookup(),
                "cogs_pct": self.db.get_cogs_percentage(),
                "owner_scope": owner_scope,
            }

        level1 = self.db.get_affiliate_product_network_snapshot(
            start_date, end_date, network_id=network_id, product_id=product_id
        )
        level2 = self.db.get_affiliate_product_funnel_network_snapshot(
            start_date, end_date, network_id=network_id, product_id=product_id
        )
        level3 = self.db.get_affiliate_quantity_snapshot(
            start_date, end_date, network_id=network_id, product_id=product_id
        )

        # Restrição por owner é aplicada em Python (não via .in_() no Postgrest):
        # um owner com centenas de afiliados gera uma URL grande demais pro
        # gateway do Supabase e derruba a query com um 400 sem corpo JSON.
        if owner_scope["restricted"]:
            owned_set = set(owned_ids)
            level1 = [r for r in level1 if r.get("affiliate_id") in owned_set]
            level2 = [r for r in level2 if r.get("affiliate_id") in owned_set]
            level3 = [r for r in level3 if r.get("affiliate_id") in owned_set]

        return {
            "level1": level1,
            "level2": level2,
            "level3": level3,
            "affiliates": self.db.get_affiliates_lookup(),
            "products": self.db.get_products_lookup(),
            "networks": self.db.get_networks_lookup(),
            "cogs_pct": self.db.get_cogs_percentage(),
            "owner_scope": owner_scope,
        }

    async def generate_and_send_affiliate_xlsx_report(
        self, target_emails: list[str], filters: dict
    ):
        """
        Gera o .xlsx do Relatório de Afiliados e envia por e-mail. Qualquer
        falha (dado inesperado, indisponibilidade do Supabase, etc.) é
        reportada por e-mail ao solicitante em vez de falhar em silêncio —
        e depois relançada, pra o ARQ ainda registrar/tentar de novo.
        """
        try:
            return await self._generate_and_send_affiliate_xlsx_report(
                target_emails, filters
            )
        except Exception as e:
            logger.exception(f"Falha ao gerar Relatório de Afiliados (.xlsx): {e}")
            period = filters.get("period", {})
            start_date = str(period.get("start_date", "?"))
            end_date = str(period.get("end_date", "?"))
            try:
                params = {
                    "from": self.settings.email_from,
                    "to": target_emails,
                    "subject": (
                        f"Relatório de Afiliados ({start_date} a {end_date}) — Falhou"
                    ),
                    "html": f"""
                    <p>Olá,</p>
                    <p>Não foi possível gerar o <strong>Relatório de Afiliados</strong>
                    para o período <strong>{start_date}</strong> a
                    <strong>{end_date}</strong>.</p>
                    <p>Detalhe técnico: <code>{type(e).__name__}: {e}</code></p>
                    <p>Verifique os filtros enviados (IDs de plataforma/produto/usuário
                    precisam ser UUIDs válidos) ou tente novamente em alguns minutos.</p>
                    <br>
                    <p>Atenciosamente,<br><strong>Tiger Offers Team</strong></p>
                    """,
                }
                await asyncio.to_thread(resend.Emails.send, params)
            except Exception:
                logger.exception(
                    "Falha ao notificar por e-mail o erro do Relatório de Afiliados."
                )
            raise

    async def _generate_and_send_affiliate_xlsx_report(
        self, target_emails: list[str], filters: dict
    ):
        period = filters.get("period", {})
        start_date = str(period["start_date"])
        end_date = str(period["end_date"])

        data = await asyncio.to_thread(self._fetch_affiliate_report_data, filters)
        owner_scope = data["owner_scope"]

        visao_geral_df = aggregator.build_visao_geral(
            data["level1"], data["affiliates"], data["cogs_pct"]
        )

        if visao_geral_df.empty:
            scope_note = (
                f" (escopo: {owner_scope['owner_label']})"
                if owner_scope["restricted"]
                else ""
            )
            logger.info(
                f"Sem dados de afiliados no período {start_date} a {end_date}"
                f"{scope_note}. Relatório xlsx não enviado."
            )
            params = {
                "from": self.settings.email_from,
                "to": target_emails,
                "subject": f"Relatório de Afiliados ({start_date} a {end_date}) — Sem dados",
                "html": f"""
                <p>Olá,</p>
                <p>Não foram encontradas vendas de afiliados no período
                <strong>{start_date}</strong> a <strong>{end_date}</strong>{scope_note}.</p>
                <p>Nenhum arquivo foi gerado.</p>
                <br>
                <p>Atenciosamente,<br><strong>Tiger Offers Team</strong></p>
                """,
            }
            await asyncio.to_thread(resend.Emails.send, params)
            return True

        detalhado_funil_df = aggregator.build_detalhado_funil(
            data["level2"], data["affiliates"], data["products"], data["networks"]
        )
        detalhado_pote_df = aggregator.build_detalhado_pote(
            data["level3"], data["affiliates"], data["products"], data["networks"]
        )
        kpis = aggregator.build_kpis(visao_geral_df)

        xlsx_bytes = await asyncio.to_thread(
            build_affiliate_report_workbook,
            visao_geral_df,
            detalhado_funil_df,
            detalhado_pote_df,
        )

        scope_html = (
            f"<p>Escopo: <strong>{owner_scope['owner_label']}</strong>.</p>"
            if owner_scope["restricted"]
            else ""
        )
        email_params = {
            "from": self.settings.email_from,
            "to": target_emails,
            "subject": f"Relatório de Afiliados — {start_date} a {end_date}",
            "html": f"""
            <p>Olá,</p>
            <p>O <strong>Relatório de Afiliados</strong> referente ao período
            <strong>{start_date}</strong> a <strong>{end_date}</strong> está pronto.</p>
            {scope_html}
            <p>Resumo do período: <strong>{kpis['total_afiliados']} afiliados</strong>,
            faturamento total de <strong>${kpis['faturamento_total']:,.2f}</strong> e
            lucro líquido de <strong>${kpis['lucro_liquido_total']:,.2f}</strong>.</p>
            <p>O arquivo em anexo contém as abas: Visão Geral, Detalhado por Funil
            e Detalhado por Pote.</p>
            <br>
            <p>Atenciosamente,<br><strong>Tiger Offers Team</strong></p>
            """,
            "attachments": [
                {
                    "filename": (
                        f"relatorio_afiliados_{start_date}_a_{end_date}.xlsx"
                    ),
                    "content": list(xlsx_bytes),
                }
            ],
        }

        response = await asyncio.to_thread(resend.Emails.send, email_params)
        logger.success(
            f"Relatório de Afiliados (.xlsx) enviado! Resend ID: {response.get('id')}"
        )
        return True

    async def generate_and_send_maxweb_refund_warning(self):
        """
        Busca afiliados da MaxWeb com >= 15% de refund (em $) nos últimos 7 dias
        e envia o alerta formatado para o Slack de forma dinâmica usando o .env.
        """
        try:
            # Importa e instancia o SlackService apenas aqui para não afetar o resto da classe
            from app.services.slack_service import SlackService
            from app.repositories.database import DatabaseRepository

            # Busca o repositório seja qual for o nome (db ou db_repo).
            # Se não existir no self, cria um novo instantaneamente
            repo = getattr(
                self, "db", getattr(self, "db_repo", DatabaseRepository(self.settings))
            )
            slack_svc = SlackService(self.settings, repo)

            # 1. Busca os dados usando a RPC do Supabase
            db_async = await create_async_client(
                self.settings.supabase_url, self.settings.supabase_key
            )
            response = await db_async.rpc("get_maxweb_high_refunds").execute()
            resultados = response.data

            if not resultados:
                logger.info(
                    "Nenhum afiliado da MaxWeb passou de 15% de refund nos últimos 7 dias, sem alerta pra disparar."
                )
                return

            logger.warning(
                f"{len(resultados)} afiliado(s) da MaxWeb acima do limite de refund. Montando o alerta pro Slack."
            )

            # 2. Constrói o layout da mensagem (Block Kit)
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "Alerta de Refund — MaxWeb",
                        "emoji": False,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Taxa de Reembolso ≥ 15% nos últimos 7 dias · {len(resultados)} ocorrência(s)",
                        }
                    ],
                },
                {"type": "divider"},
            ]

            for item in resultados:
                aff_id = item.get("aff_id")
                aff_name = item.get("aff_name")
                produto = item.get("produto")
                vendas = float(item.get("valor_total_vendas", 0))
                refunds = float(item.get("valor_total_refunds", 0))
                taxa = float(item.get("taxa_refund_percentual", 0))

                # Indicador único de severidade por item (não por linha)
                severidade = "🔴 Crítico" if taxa >= 50 else "🟠 Atenção"

                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{aff_name}*  `{aff_id}`\n{severidade} — *{taxa:.1f}%* de refund",
                        },
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Produto:*\n{produto}"},
                            {"type": "mrkdwn", "text": f"*Vendas:*\n${vendas:,.2f}"},
                            {
                                "type": "mrkdwn",
                                "text": f"*Reembolsado:*\n${refunds:,.2f}",
                            },
                            {"type": "mrkdwn", "text": f"*Taxa:*\n{taxa:.1f}%"},
                        ],
                    }
                )
                blocks.append({"type": "divider"})

            # 3. Puxa os alvos do .env e limpa os espaços vazios
            targets_string = getattr(self.settings, "slack_maxweb_targets", "")
            targets = [t.strip() for t in targets_string.split(",") if t.strip()]

            if not targets:
                logger.warning(
                    "SLACK_MAXWEB_TARGETS não tá configurado no .env, não tem pra quem mandar o alerta."
                )
                return

            # 4. Envia para cada pessoa/canal da lista usando a instância local (slack_svc)
            sucesso_count = 0
            for target in targets:
                sucesso = slack_svc.send_message(
                    channel=target,
                    text="Alerta de Refund - MaxWeb",
                    blocks=blocks,
                    username="MaxWeb Refund Monitor",
                    # icon_emoji=":chart_with_downwards_trend:",
                    icon_url="https://tigeroffers.com/assets/img/top-tiger.png",
                )
                if sucesso:
                    sucesso_count += 1
                    logger.debug(f"Alerta MaxWeb enviado pra: {target}")
                else:
                    logger.error(f"Falha ao enviar alerta MaxWeb pra: {target}")

            logger.info(
                f"Alerta da MaxWeb concluído, entregue a {sucesso_count}/{len(targets)} destinos."
            )

        except Exception as e:
            logger.exception(
                f"Problemas ao gerar/enviar o alerta de refund da MaxWeb: {e}"
            )
