"""
Sincroniza as URLs de checkout (BuyGoods) guardadas em `checkouts` no Supabase
para as páginas HTML publicadas no servidor.

Como funciona:
  1. Recebe product_id, network_id, domain, active_funnel_number e
     previous_funnel_number do frontend. Uma Edge Function do Supabase já
     inseriu a linha correspondente em `product_network_active_funnels`
     antes de chamar este serviço — o id dela (active_funnel_row_id) também
     vem no payload. Este serviço NÃO insere/faz upsert nessa tabela, só
     atualiza `status`/`error_message` da linha indicada com o resultado da
     tentativa.
  2. Busca em `checkouts` todas as linhas desse product_id + network_id +
     active_funnel_number, montando um mapa (funnel_stage, switch_link) ->
     url. switch_link não é uma coluna — é derivado como
     f"switchLink-{quantity}b" a partir de checkouts.quantity (convenção
     alinhada com quem cria os codenames dos produtos).
  3. Varre recursivamente {hosting_base}/{domain}/public_html atrás de
     arquivos .html.
  4. Em cada arquivo, encontra tags <a id="switchLink-..."> e troca APENAS o
     valor do atributo href pelo valor correspondente do mapa — o resto do
     arquivo não é tocado. O estágio do funil (Purchase, Up1, Dw1, ...) vem
     do atributo data-funnel-stage da mesma tag (default "Purchase" se
     ausente).
  5. Ao final, sempre atualiza status ('success' ou 'error') e
     error_message em `product_network_active_funnels` (via
     active_funnel_row_id). Em caso de sucesso e se o funil realmente mudou
     (previous_funnel_number != active_funnel_number), registra a transição
     em `active_funnel_history` — essa tabela continua de responsabilidade
     do backend.

Porta a lógica do script standalone /python/funil/sync.py para dentro do
dashboard, reaproveitando o client Supabase do DatabaseRepository e as
credenciais SSH já usadas pelo WebScannerService.
"""

import asyncio
import difflib
import os
import posixpath
import re
import stat
from datetime import datetime, timezone

import paramiko
from loguru import logger

from app.config import Settings
from app.repositories.database import DatabaseRepository

TAG_RE = re.compile(r"<a\b[^>]*?>", re.IGNORECASE | re.DOTALL)
ATTR_RE = re.compile(
    r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"'
    r"|([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*'([^']*)'"
)
HREF_RE = re.compile(r'(href\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)


class FunnelSyncError(Exception):
    """Erro esperado (produto/checkout não encontrado, config faltando)."""


def _parse_attrs(tag_text: str) -> dict:
    attrs = {}
    for m in ATTR_RE.finditer(tag_text):
        if m.group(1) is not None:
            attrs[m.group(1).lower()] = m.group(2)
        else:
            attrs[m.group(3).lower()] = m.group(4)
    return attrs


def _build_new_content(
    original: str, checkout_map: dict, remote_path: str, warnings: list[str]
) -> str | None:
    """Troca hrefs nas tags <a id="..."> cujo id bate com switch_link. Retorna None se nada mudou."""
    known_switch_links = {switch_link for (_, switch_link) in checkout_map}

    parts = []
    last_end = 0
    changed = False

    for m in TAG_RE.finditer(original):
        tag_text = m.group(0)
        attrs = _parse_attrs(tag_text)
        switch_link = attrs.get("id")
        if not switch_link or switch_link not in known_switch_links:
            continue
        stage = attrs.get("data-funnel-stage", "Purchase")

        new_url = checkout_map.get((stage, switch_link))
        if not new_url:
            warnings.append(
                f"{remote_path}: sem checkout cadastrado para "
                f"funnel_stage='{stage}' switch_link='{switch_link}' — link mantido como está"
            )
            continue

        href_match = HREF_RE.search(tag_text)
        if not href_match:
            warnings.append(
                f"{remote_path}: tag com id='{switch_link}' não tem href — pulando"
            )
            continue

        old_href = href_match.group(3)
        if old_href == new_url:
            continue

        tag_start = m.start()
        href_value_start = tag_start + href_match.start(3)
        href_value_end = tag_start + href_match.end(3)

        parts.append(original[last_end:href_value_start])
        parts.append(new_url)
        last_end = href_value_end
        changed = True

    if not changed:
        return None

    parts.append(original[last_end:])
    return "".join(parts)


class FunnelSyncService:
    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        self.settings = settings
        self.db_repo = db_repo

    # --- Supabase: leitura ---

    def _fetch_product(self, product_id: str) -> dict:
        response = (
            self.db_repo.client.table("products")
            .select("id, name")
            .eq("id", product_id)
            .execute()
        )
        if not response.data:
            raise FunnelSyncError(f"Produto '{product_id}' não encontrado.")
        return response.data[0]

    def _fetch_checkout_map(
        self, product_id: str, network_id: str, funnel_number: int
    ) -> dict:
        """Retorna { (funnel_stage, switch_link): url }.

        switch_link não é mais uma coluna própria — é derivado de
        checkouts.quantity, já que o id da tag no HTML sempre segue o padrão
        switchLink-{quantity}b (convenção alinhada com quem cria os
        codenames). Filtra allow_aff_link=true porque pode haver mais de um
        checkout com o mesmo (funnel_stage, quantity) — ex: a variante _sms
        de um mesmo pote — e só a linha "principal" (allow_aff_link=true)
        deve virar o switchLink do HTML.
        """
        response = (
            self.db_repo.client.table("checkouts")
            .select("funnel_stage, quantity, url")
            .eq("product_id", product_id)
            .eq("network_id", network_id)
            .eq("funnel_number", funnel_number)
            .eq("allow_aff_link", True)
            .execute()
        )

        mapping = {}
        for row in response.data:
            quantity = row.get("quantity")
            if not quantity:
                continue
            switch_link = f"switchLink-{quantity}b"
            key = (row["funnel_stage"] or "Purchase", switch_link)
            mapping[key] = row["url"]
        return mapping

    # --- Supabase: escrita (não deve derrubar o sync se falhar) ---

    def _update_sync_status(
        self, active_funnel_row_id: str, status: str, error_message: str | None
    ) -> None:
        try:
            self.db_repo.client.table("product_network_active_funnels").update(
                {"status": status, "error_message": error_message}
            ).eq("id", active_funnel_row_id).execute()
        except Exception as e:
            logger.error(
                f"[funnel-sync] falha ao atualizar status em "
                f"product_network_active_funnels (id={active_funnel_row_id}): {e}"
            )

    def _log_funnel_change(
        self,
        product_id: str,
        network_id: str,
        previous_funnel: int | None,
        new_funnel: int,
        user_id: str,
    ) -> None:
        try:
            self.db_repo.client.table("active_funnel_history").insert(
                {
                    "product_id": product_id,
                    "network_id": network_id,
                    "active_funnel_number": new_funnel,
                    "previous_active_funnel_number": previous_funnel,
                    "changed_by": user_id,
                }
            ).execute()
        except Exception as e:
            logger.error(f"[funnel-sync] falha ao registrar active_funnel_history: {e}")

    # --- SFTP ---

    def _connect_sftp(self):
        transport = paramiko.Transport(
            (self.settings.ssh_host, self.settings.ssh_port)
        )
        transport.connect(
            username=self.settings.ssh_username, password=self.settings.ssh_password
        )
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise RuntimeError("Não foi possível abrir sessão SFTP.")
        return sftp, transport

    def _walk_remote_html_files(self, sftp, remote_dir: str):
        for entry in sftp.listdir_attr(remote_dir):
            remote_path = posixpath.join(remote_dir, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                yield from self._walk_remote_html_files(sftp, remote_path)
            elif entry.filename.lower().endswith((".html", ".htm")):
                yield remote_path

    @staticmethod
    def _read_remote_file(sftp, remote_path: str) -> str:
        with sftp.open(remote_path, "r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        return content

    def _backup_file(self, remote_path: str, content: str) -> str:
        backup_dir = os.path.join(self.settings.temp_dir, "funnel_sync_backups")
        os.makedirs(backup_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = remote_path.strip("/").replace("/", "__")
        backup_path = os.path.join(backup_dir, f"{ts}__{safe_name}")
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(content)
        return backup_path

    # --- Fluxo principal (bloqueante — roda em thread) ---

    def _run_sync(
        self,
        product_id: str,
        network_id: str,
        domain: str,
        user_id: str,
        active_funnel_number: int,
        previous_funnel_number: int | None,
        active_funnel_row_id: str,
    ) -> dict:
        warnings: list[str] = []
        files_changed: list[dict] = []
        checkout_map: dict = {}

        try:
            product = self._fetch_product(product_id)

            checkout_map = self._fetch_checkout_map(
                product_id, network_id, active_funnel_number
            )
            if not checkout_map:
                raise FunnelSyncError(
                    f"Nenhum checkout cadastrado para product_id={product_id} "
                    f"network_id={network_id} funnel_number={active_funnel_number}."
                )

            remote_root = f"{self.settings.hosting_base}/{domain}/public_html"
            logger.info(
                f"[funnel-sync] Varrendo {remote_root} "
                f"(produto={product['name']}, network_id={network_id}, "
                f"funil={active_funnel_number})"
            )

            sftp, transport = self._connect_sftp()
            try:
                for remote_path in self._walk_remote_html_files(sftp, remote_root):
                    original = self._read_remote_file(sftp, remote_path)
                    new_content = _build_new_content(
                        original, checkout_map, remote_path, warnings
                    )
                    if new_content is None:
                        continue

                    backup_path = self._backup_file(remote_path, original)
                    with sftp.open(remote_path, "w") as f:
                        f.write(new_content)

                    verify = self._read_remote_file(sftp, remote_path)
                    ok = verify == new_content

                    diff = "".join(
                        difflib.unified_diff(
                            original.splitlines(keepends=True),
                            new_content.splitlines(keepends=True),
                            lineterm="",
                        )
                    )

                    files_changed.append(
                        {
                            "path": remote_path,
                            "ok": ok,
                            "backup_path": backup_path,
                            "diff": diff,
                        }
                    )
                    log = logger.info if ok else logger.error
                    log(
                        f"[funnel-sync] {'OK' if ok else 'FALHOU'} {remote_path} "
                        f"(backup: {backup_path})"
                    )
            finally:
                sftp.close()
                transport.close()
        except Exception as e:
            self._update_sync_status(active_funnel_row_id, "error", str(e))
            raise
        else:
            self._update_sync_status(active_funnel_row_id, "success", None)
            if previous_funnel_number != active_funnel_number:
                self._log_funnel_change(
                    product_id,
                    network_id,
                    previous_funnel_number,
                    active_funnel_number,
                    user_id,
                )

        return {
            "product": product["name"],
            "domain": domain,
            "network_id": network_id,
            "funnel_number": active_funnel_number,
            "checkouts_found": len(checkout_map),
            "warnings": warnings,
            "files_changed": files_changed,
        }

    async def apply(
        self,
        product_id: str,
        network_id: str,
        domain: str,
        user_id: str,
        active_funnel_number: int,
        previous_funnel_number: int | None,
        active_funnel_row_id: str,
    ) -> dict:
        """Offload da operação bloqueante (SSH/SFTP) para uma thread separada."""
        return await asyncio.to_thread(
            self._run_sync,
            product_id,
            network_id,
            domain,
            user_id,
            active_funnel_number,
            previous_funnel_number,
            active_funnel_row_id,
        )
