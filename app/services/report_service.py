import asyncio
import io
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML
import resend
from loguru import logger

from app.config import Settings
from app.repositories.database import DatabaseRepository


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

        # 2. Renderiza o HTML (Passamos a lista plana 'records')
        context = {
            "days_limit": days_limit,
            "total_products": unique_products,
            "total_affiliates": len(records),
            "total_risk_volume": total_risk_volume,
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
            "subject": f"Alerta: {len(records)} afiliados sem vendas há mais de {days_limit} dias",
            "html": f"""
            <p>Olá, time de afiliados,</p>

            <p>O relatório <strong>Afiliados sem vendas</strong> está disponível.</p>

            <p>Identificamos <strong>{len(records)} afiliados ativos</strong> que não registraram vendas 
            há mais de <strong>{days_limit} dias</strong>.</p>

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
