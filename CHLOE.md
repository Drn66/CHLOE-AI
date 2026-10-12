# CHLOE AI

Assistente pessoal para desktop, derivado de MARK LV de FatihMakes. Esta versão
usa uma interface em português, fundo preto, esfera de partículas com reação
ao áudio e painel de conversa que pode ser ocultado. A integração continua
utilizando Gemini Live e a chave de API fornecida pelo usuário.

O [readme original](readme.md) foi preservado como referência do projeto de
origem. Algumas descrições visuais ali se referem à interface anterior.
A [licença original](LICENSE) e a atribuição a FatihMakes também foram mantidas.

## Executar em outro computador

No Windows, instale Git e Python. O instalador do projeto exige Python 3.11 ou
superior e declara testes até 3.13; a instalação local também foi executada com
Python 3.14. Para uma instalação nova, Python 3.13 é compatível com a faixa
declarada pelo instalador.

Depois de clonar o repositório e entrar na pasta, execute no PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe setup.py
.\.venv\Scripts\python.exe -B main.py
```

O `setup.py` instala `requirements.txt` e tenta instalar os navegadores do
Playwright. Esses navegadores são necessários para a automação web e podem ser
instalados depois com:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium firefox
```

Na primeira execução, use **Conectar API** para informar sua chave Gemini.
Ela será salva somente no arquivo local `config/api_keys.json`, ignorado pelo
Git. A aplicação não carrega `.env`: sua configuração existente é em JSON.

Para configurar manualmente, copie `config/api_keys.example.json` para
`config/api_keys.json` **somente se o arquivo de destino ainda não existir**.
Preencha `gemini_api_key` localmente. Nunca coloque uma chave no arquivo de
exemplo. Para outros sistemas, `os_system` aceita `mac` e `linux`, além de
`windows`; o foco desta versão é o desktop Windows.

Os nomes de usuário e dispositivos foram deixados vazios no exemplo.
Selecione o microfone e a saída de áudio do novo computador em **Controles**.
O exemplo inicia com o microfone desativado: alguns headsets Bluetooth
silenciam a saída estéreo ao abrir seu microfone. Isso não impede respostas
faladas a mensagens digitadas. A voz padrão é `Aoede`, com instruções em pt-BR.

## Verificações

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Os testes não usam credenciais nem fazem chamadas reais ao Gemini. As
dependências não estão travadas em versões exatas; `requirements.txt` contém
as faixas e as dependências opcionais da aplicação.

## Dados que ficam neste computador

- `config/api_keys.json`: chave de API, preferências e possíveis credenciais de plugins.
- `config/certs/`: certificado e chave privada do painel remoto.
- `memory/*.json`: memória pessoal e histórico persistente.
- Sessões de autenticação, tokens OAuth, uploads, capturas, gravações e logs.
- Ambientes virtuais, caches e configurações de editor.

Não copie esses dados para o GitHub. Configure a chave e os dispositivos
separadamente em cada computador. A memória pessoal não será sincronizada
pelo repositório.
