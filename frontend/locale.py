"""Presentation-only translations for known assistant status events."""

def localize_event(text):
    replacements = {
        "JARVIS online — sleeping. Say 'Hey Jarvis' to wake me.": "CHLOE AI está em repouso. Use Despertar nos controles ou a frase de ativação configurada.",
        "Phone connected via Remote Dashboard.": "Celular conectado pelo painel remoto.",
        "Proactive audio unavailable — reconnecting without it.": "Áudio proativo indisponível — reconectando sem esse recurso.",
        "Every live model is rate-limited — retrying.": "Os modelos de voz atingiram o limite de uso. Tentando novamente.",
        "Switching to ": "Alternando para ",
        " — the previous model is out of quota.": " — o modelo anterior atingiu o limite de uso.",
        " unavailable — using system default.": " indisponível — usando o padrão do sistema.",
        "Input device:": "Dispositivo de entrada:",
        "Output device:": "Dispositivo de saída:",
        "Microphone '": "Microfone '",
        "Speakers '": "Alto-falantes '",
        "JARVIS online.": "CHLOE AI está pronta.",
        "Microphone muted.": "Microfone desativado.",
        "Microphone active.": "Microfone ativado.",
        "Reconnected — conversation restored.": "Conexão restabelecida — conversa restaurada.",
        "Could not restore the conversation — starting fresh.": "Não foi possível restaurar a conversa. Iniciando uma nova sessão.",
        "API key invalid — please re-enter your key.": "Chave de API inválida. Informe uma nova chave nas configurações.",
        "Shutdown requested.": "Encerramento solicitado.",
        "Desktop shortcut created.": "Atalho criado na área de trabalho.",
        "Dashboard not running — remote unavailable.": "Controle remoto indisponível.",
        "Awaiting confirmation": "Aguardando confirmação",
        "Confirmation expired": "Confirmação expirada",
        "Confirmed": "Confirmado",
        "Cancelled": "Cancelado",
        "Connection failed — retrying in": "Falha na conexão — nova tentativa em",
        "(a VPN may be required)": "(verifique sua conexão de rede)",
        "I'm asleep — say 'Hey Jarvis' or tap WAKE NOW first.": "Estou em repouso. Use Despertar nos controles ou diga a frase de ativação configurada.",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.replace("JARVIS", "CHLOE AI")
