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

## Atalho no Windows

Com o ambiente `.venv` instalado, o botão **Criar atalho na área de trabalho**
gera **CHLOE AI**, usando `.venv\Scripts\pythonw.exe` e `launch_chloe.py`.
O inicializador executa `main.py` na pasta do projeto, sem terminal. Se já
houver uma instância aberta nessa pasta, o atalho tenta trazê-la para frente.
Se essa instância foi aberta antes da última edição do código, ela ainda roda a
versão antiga: o atalho avisa e oferece reiniciar com a versão atual.
Feche pelo **X** da janela ou **Alt+F4**. O atalho não ativa início automático.

Falhas de execução são exibidas em uma caixa de diálogo; `logs/launcher.log`
registra horário, código do evento e código numérico de saída. Um erro interno
que não encerra o app (em um evento da interface, numa thread ou numa tarefa
asyncio) também é registrado com um código específico, como `ui_runtime_error`
ou `worker_io_error`, e mais `ref`, thread, origem e a pilha de chamadas. A pilha
mostra só arquivo relativo ao projeto, linha e função. Nesse caso o aviso diz
que a CHLOE continua aberta e aparece uma vez por erro em cada sessão.
Cada abertura registra também o interpretador, o script, a data do código
carregado, o código de saída do processo da CHLOE e as últimas linhas do seu
stderr nativo (Qt e falhas fatais do Python), com a pasta do usuário, query
strings e chaves omitidas. Mensagens de exceção e credenciais não são gravadas
pelo inicializador. Mantenha a pasta do projeto e seu ambiente no mesmo local;
se movê-los, recrie o ambiente e o atalho no destino.

## Interface e inicialização

A conversa começa recolhida em instalações novas. Use **Conversa**, no topo,
para abrir ou ocultar o painel; arraste o divisor para ajustar sua largura.
A escolha e a largura ficam em `config/ui_preferences.json`, ignorado pelo Git
e separado das credenciais. Recolher o painel preserva as mensagens, o rascunho
e o anexo; microfone e interrupção continuam acessíveis no rodapé.

A faixa superior mostra horário local, CPU, RAM e GPU. As leituras são
compartilhadas com o monitor existente: CPU/RAM a cada 2 segundos e GPU/
temperatura a cada 6 segundos, em segundo plano. **Indisponível** significa
que não há uma leitura válida; 0% só aparece quando foi medido.

Em **Controles → Inicialização · saudação**, escolha se quer uma saudação,
silenciosa ou falada, e quais dados incluir. O padrão é uma saudação silenciosa
com horário. Ela ocorre no máximo uma vez por abertura, após conectar; uma
reconexão não a repete. Se você começar a interagir ou a CHLOE estiver em
repouso, a saudação é dispensada. Sem saída de áudio pronta, a opção falada
recorre ao texto. A voz continua sendo gerada pelo Gemini existente.

Notícias não são buscadas automaticamente na inicialização. Você ainda pode
pedir notícias na conversa. Preferências antigas do resumo matinal não
reativam a busca automática. Memória pessoal e credenciais não são migradas
nem regravadas para armazenar essas novas preferências.

## Ilha Dinâmica

O botão **Ilha**, no topo, recolhe a CHLOE para uma pílula flutuante no topo
central da tela, sempre visível sobre outros programas e fora do Alt+Tab e da
barra de tarefas. Ela não rouba o foco: o teclado só vai para a ilha quando você
clica no campo de texto.

- Passe o mouse para expandir; ao sair, ela recolhe após 400 ms.
- Gire a rodinha do mouse para trocar de andar: **Assistente** (última resposta,
  campo de texto e microfone, ligado ao mesmo mudo do F4), **Clima** e **Mídia**.
- Se algo estiver tocando no Windows (Spotify, YouTube no navegador, VLC…), a
  pílula mostra a capa e um equalizador, e o andar de mídia abre primeiro.
- **⤢** ou o clique direito → **Abrir janela completa** restaura a janela como
  estava. Clique direito → **Encerrar CHLOE** fecha o app.

O player usa a API de mídia do Windows (pacotes `winrt-*` do `requirements.txt`).
Sem eles, a ilha funciona e o andar de mídia mostra "Nada tocando agora". O clima
só começa a ser consultado quando a ilha é aberta pela primeira vez: a cidade é
estimada pelo IP público (ipinfo.io, com ip-api.com de reserva) e o tempo vem do
Open-Meteo, a cada 15 minutos. Nenhuma chave de API é usada nem gravada.

## Verificações

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

Os testes não usam credenciais nem fazem chamadas reais ao Gemini. As
dependências não estão travadas em versões exatas; `requirements.txt` contém
as faixas e as dependências opcionais da aplicação.

Os testes do painel incluem regressões do JavaScript real em Node.js, sem
pacotes npm. Instale Node.js para executá-las; sem ele, o teste é marcado como
ignorado. Os testes de interface web usam Chromium isolado do Playwright:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_dashboard_browser.py -v
```

Há também um smoke test da janela desktop real, com configuração temporária,
sem credenciais, backend Gemini ou captura de microfone. A janela fecha sozinha:

```powershell
.\.venv\Scripts\python.exe -B tests\smoke_desktop.py --seconds 8
```

## Segurança e recuperação do painel remoto

O painel aceita somente HTTPS/WSS. Certificado ausente, inválido, expirado ou
incompatível com a chave desativa o painel; a aplicação desktop continua
disponível. Não há fallback para HTTP na rede. Use certificado confiável no
celular para habilitar os recursos de voz, preservando as proteções do navegador.

A aplicação não altera firewall ou perfil de rede e não solicita elevação para
liberar acesso. Caso a rede bloqueie o painel, a configuração deve ser feita
explicitamente pelo responsável, limitada a TCP 8000, perfil privado e rede
local. Regras antigas de versões anteriores não são removidas automaticamente.

Revogar um dispositivo invalida suas sessões, conexões e comandos ainda não
iniciados. O bearer expira em oito horas e a credencial de reconexão em 30 dias;
reiniciar o servidor invalida ambos. Uma ação já aceita pelo Gemini antes da
revogação não pode ser desfeita pelo mecanismo de autenticação.

Uma mensagem remota só aparece como entregue após o aceite do transporte ao
Gemini. Em caso de resposta incerta, use **Consultar envio**: o mesmo ID é
reutilizado para impedir execução duplicada. Falhas preservam o rascunho.
Configurações usam gravações atômicas; se o arquivo estiver ilegível ou sem
acesso, ele é preservado e a interface permite corrigir o problema e tentar
novamente. Atalho, auto-start Windows e execução direta de `main.py` usam o
supervisor; fechar a janela encerra o runtime e seus recursos.

## Dados que ficam neste computador

- `config/api_keys.json`: chave de API, preferências e possíveis credenciais de plugins.
- `config/certs/`: certificado e chave privada do painel remoto.
- `memory/*.json`: memória pessoal e histórico persistente.
- Sessões de autenticação, tokens OAuth, uploads, capturas, gravações e logs.
- Ambientes virtuais, caches e configurações de editor.

Não copie esses dados para o GitHub. Configure a chave e os dispositivos
separadamente em cada computador. A memória pessoal não será sincronizada
pelo repositório.
