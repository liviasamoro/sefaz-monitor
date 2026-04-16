"""
sefaz_monitor.py
Monitora páginas do portal NF-e (nfe.fazenda.gov.br) e envia alertas
por e-mail e/ou Telegram quando detecta mudanças no conteúdo.

Credenciais lidas de variáveis de ambiente — compatível com GitHub Secrets.

Uso local:
    export SMTP_USUARIO="seu@email.com"
    export SMTP_SENHA="senha_de_app"
    export TELEGRAM_TOKEN="token_do_bot"
    export TELEGRAM_CHAT_ID="seu_chat_id"
    python sefaz_monitor.py

Agendamento via GitHub Actions: veja monitor.yml
"""

import hashlib
import json
import logging
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# PÁGINAS A MONITORAR
# ---------------------------------------------------------------------------

PAGINAS = [
    {
        "nome": "Portal NF-e — Página principal",
        "url": "https://www.nfe.fazenda.gov.br/portal/principal.aspx",
    },
    {
        "nome": "Portal NF-e — Notas Técnicas",
        "url": "https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=NR",
    },
    {
        "nome": "Portal NF-e — Manuais de Orientação",
        "url": "https://www.nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=MO",
    },
]

# ---------------------------------------------------------------------------
# CREDENCIAIS — lidas do ambiente (GitHub Secrets ou export local)
# ---------------------------------------------------------------------------

def _env(chave: str, padrao: str = "") -> str:
    return os.environ.get(chave, padrao).strip()


# E-mail
EMAIL_ATIVO    = bool(_env("SMTP_USUARIO"))
SMTP_SERVIDOR  = _env("SMTP_SERVIDOR", "smtp.gmail.com")
SMTP_PORTA     = int(_env("SMTP_PORTA", "587"))
SMTP_USUARIO   = _env("SMTP_USUARIO")
SMTP_SENHA     = _env("SMTP_SENHA")
EMAIL_DESTINO  = _env("EMAIL_DESTINO") or SMTP_USUARIO

# Telegram
TELEGRAM_ATIVO   = bool(_env("TELEGRAM_TOKEN"))
TELEGRAM_TOKEN   = _env("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")

# Arquivos de estado e log
ARQUIVO_ESTADO = Path(__file__).parent / "sefaz_hashes.json"
ARQUIVO_LOG    = Path(__file__).parent / "sefaz_monitor.log"

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO DE LOG
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(ARQUIVO_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FUNÇÕES AUXILIARES
# ---------------------------------------------------------------------------

def carregar_estado() -> dict:
    if ARQUIVO_ESTADO.exists():
        try:
            with open(ARQUIVO_ESTADO, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"Não foi possível ler o arquivo de estado: {e}")
    return {}


def salvar_estado(estado: dict) -> None:
    try:
        with open(ARQUIVO_ESTADO, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except IOError as e:
        log.error(f"Erro ao salvar estado: {e}")


def calcular_hash(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def buscar_pagina(url: str, timeout: int = 30) -> bytes | None:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resposta = requests.get(url, headers=headers, timeout=timeout)
        resposta.raise_for_status()
        return resposta.content
    except requests.RequestException as e:
        log.error(f"Erro ao acessar {url}: {e}")
        return None

# ---------------------------------------------------------------------------
# ENVIO DE ALERTAS
# ---------------------------------------------------------------------------

def enviar_email(paginas_alteradas: list[dict]) -> None:
    if not EMAIL_ATIVO:
        log.info("E-mail desativado (SMTP_USUARIO não definido).")
        return

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    assunto = f"[SEFAZ] Atualização detectada — {agora}"

    linhas_html = "".join(
        f"<li><b>{p['nome']}</b><br><a href='{p['url']}'>{p['url']}</a></li>"
        for p in paginas_alteradas
    )
    corpo_html = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333">
      <h2 style="color:#c0392b">Atualização detectada no portal NF-e / SEFAZ</h2>
      <p>Uma ou mais páginas monitoradas tiveram seu conteúdo alterado em <b>{agora}</b>.</p>
      <ul>{linhas_html}</ul>
      <p>Acesse os links acima para verificar as novas normas técnicas publicadas.</p>
      <hr><small>Monitoramento automático — sefaz_monitor.py via GitHub Actions</small>
    </body></html>
    """
    linhas_txt = "\n".join(f"- {p['nome']}: {p['url']}" for p in paginas_alteradas)
    corpo_txt = (
        f"Atualização detectada no portal NF-e / SEFAZ em {agora}.\n\n"
        f"Páginas alteradas:\n{linhas_txt}\n\n"
        "Acesse os links para verificar as novas publicações."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = SMTP_USUARIO
    msg["To"]      = EMAIL_DESTINO
    msg.attach(MIMEText(corpo_txt, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_SERVIDOR, SMTP_PORTA) as servidor:
            servidor.ehlo()
            servidor.starttls()
            servidor.login(SMTP_USUARIO, SMTP_SENHA)
            servidor.sendmail(SMTP_USUARIO, EMAIL_DESTINO, msg.as_string())
        log.info(f"E-mail enviado para {EMAIL_DESTINO}")
    except smtplib.SMTPException as e:
        log.error(f"Falha ao enviar e-mail: {e}")


def enviar_telegram(paginas_alteradas: list[dict]) -> None:
    if not TELEGRAM_ATIVO:
        log.info("Telegram desativado (TELEGRAM_TOKEN não definido).")
        return

    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = "\n".join(f"• {p['nome']}\n  {p['url']}" for p in paginas_alteradas)
    texto = (
        f"*SEFAZ — Atualização detectada*\n_{agora}_\n\n"
        f"Páginas alteradas:\n{linhas}\n\n"
        "Verifique novas normas técnicas publicadas."
    )

    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url_api, json=payload, timeout=15)
        resp.raise_for_status()
        log.info(f"Mensagem Telegram enviada para chat_id {TELEGRAM_CHAT_ID}")
    except requests.RequestException as e:
        log.error(f"Falha ao enviar Telegram: {e}")

# ---------------------------------------------------------------------------
# LÓGICA PRINCIPAL
# ---------------------------------------------------------------------------

def monitorar() -> None:
    log.info("=" * 60)
    log.info("Iniciando monitoramento do portal SEFAZ / NF-e")
    log.info(f"E-mail ativo: {EMAIL_ATIVO} | Telegram ativo: {TELEGRAM_ATIVO}")
    log.info(f"Páginas configuradas: {len(PAGINAS)}")

    estado = carregar_estado()
    paginas_alteradas = []

    for pagina in PAGINAS:
        nome = pagina["nome"]
        url  = pagina["url"]
        log.info(f"Verificando: {nome}")

        conteudo = buscar_pagina(url)
        if conteudo is None:
            log.warning(f"  Pulando '{nome}' — falha na requisição.")
            continue

        hash_atual    = calcular_hash(conteudo)
        hash_anterior = estado.get(url)

        if hash_anterior is None:
            log.info("  Primeira verificação — hash salvo (sem alerta).")
            estado[url] = hash_atual
        elif hash_atual != hash_anterior:
            log.info("  MUDANÇA DETECTADA.")
            paginas_alteradas.append(pagina)
            estado[url] = hash_atual
        else:
            log.info("  Sem alterações.")

    salvar_estado(estado)

    if paginas_alteradas:
        log.info(f"{len(paginas_alteradas)} página(s) alterada(s) — enviando alertas...")
        enviar_email(paginas_alteradas)
        enviar_telegram(paginas_alteradas)
    else:
        log.info("Nenhuma alteração detectada. Nenhum alerta enviado.")

    log.info("Monitoramento concluído.")
    log.info("=" * 60)


if __name__ == "__main__":
    monitorar()
