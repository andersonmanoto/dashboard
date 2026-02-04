import asyncio
import json
import paramiko
from loguru import logger
from app.config import get_settings

class WebScannerService:
    """
    Serviço de Scanner Remoto via SSH.
    
    Substitui o crawler local por uma chamada RPC (Remote Procedure Call)
    via SSH que executa um script Python no servidor de destino.
    """

    def __init__(self):
        settings = get_settings()
        self.host = settings.ssh_host
        self.username = settings.ssh_username
        self.password = settings.ssh_password
        self.port = settings.ssh_port
        self.script_path = settings.remote_script_path

    def _execute_ssh_command(self, domain: str, codename: str, debug: bool):
        """
        Lógica síncrona (bloqueante) do Paramiko.
        """
        ssh = None
        try:
            # 1. Conexão
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            if debug:
                logger.info(f"Conectando SSH em {self.host}:{self.port}...")
                
            ssh.connect(
                self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=10
            )

            # 2. Comando
            cmd = f"python3 {self.script_path} '{domain}' '{codename}'"
            
            if debug:
                logger.info(f"Executando comando remoto: {cmd}")

            stdin, stdout, stderr = ssh.exec_command(cmd)
            
            # Lê saídas
            output = stdout.read().decode().strip()
            error_log = stderr.read().decode().strip()

            if error_log and debug:
                logger.warning("Logs do script remoto:")
                for line in error_log.splitlines():
                    logger.warning(f"  [REMOTE] {line}")

            if debug:
                logger.success("Comando remoto finalizado.")

            # 3. Tratamento do Retorno
            # Tenta converter para JSON (lista/dict) se o script retornar estruturado.
            # Se não, retorna a string crua.
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return output

        except Exception as e:
            logger.error(f"Erro na execução SSH: {e}")
            return {"error": str(e)}
        finally:
            if ssh:
                ssh.close()

    async def run_scan(self, codename: str, domain_filter: str, debug: bool = False):
        """
        Executa o scan de forma assíncrona (Non-blocking).
        
        Mantém a assinatura do método original para não quebrar o app/main.py.
        """
        if not domain_filter:
            return {"error": "Domain filter é obrigatório para este método remoto."}

        # Offload da operação bloqueante de I/O (SSH) para uma thread separada
        return await asyncio.to_thread(
            self._execute_ssh_command, 
            domain=domain_filter, 
            codename=codename, 
            debug=debug
        )