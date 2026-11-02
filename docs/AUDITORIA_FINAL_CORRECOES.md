# Auditoria final e correções — CHLOE AI

Data: 1 de novembro de 2026. Ambiente: Windows, Python 3.14, repositório local
`chloe-ai/`, HEAD inicial `ae9d7cd`. Este relatório corresponde ao working tree,
incluindo trabalho local anterior; não houve commit, push, merge ou publicação.

## 1. Resumo executivo

Foram tratados os 11 problemas originais: quatro altos e sete médios.
As correções preservam Gemini, conversa, voz, painel, configuração e identidade
visual. A segunda banca encontrou defeitos adicionais que entraram em novo
ciclo de correção e revalidação. O parecer definitivo está na seção 9.

### Estado inicial e preservação

- Baseline: 58 testes existentes aprovados em 4,551 s; `pip check` sem conflitos.
- Existiam oito arquivos modificados e nove arquivos novos não commitados.
- `git status`, `git diff` e `git log -1` foram inspecionados antes das correções.
- Recuperação: `../.codex-recovery/before-integral-fixes-20261101-053348/`.
  Contém 77 arquivos de código/documentação, patch binário, status, HEAD e
  manifesto SHA-256. Não contém credenciais ignoradas, memória, certificados,
  logs pessoais nem o ambiente virtual.
- Não houve reset, clean, checkout destrutivo, stash ou descarte de trabalho.

## 2. Correções dos 11 problemas

| ID | Gravidade original | Descrição | Causa raiz | Arquivo e linha atual | Correção | Testes | Evidência | Status |
|---|---|---|---|---|---|---|---|---|
| 01 | Alta | Revogar deixava bearer e sockets ativos. | Credenciais sem vínculo completo de dispositivo e validade. | `dashboard/server.py:475`; `main.py:2099` | Sessão por dispositivo, expiração, revogação dos transportes/filas e recheck antes do envio. | test_dashboard_security.py; test_dashboard_delivery.py | HTTP/WS/relogin negados; outro dispositivo preservado. | Corrigido e validado |
| 02 | Alta | Falha TLS ligava HTTP em todas as interfaces. | Fallback de transporte aberto. | `dashboard/server.py:221`; `dashboard/server.py:918` | Validar parsing/datas/chave/contexto TLS; falha impede listener; sem alias HTTP. | test_dashboard_security.py; test_dashboard_tls_socket.py | Certificados inválidos não escutam; HTTPS/WSS reais funcionam. | Corrigido e validado |
| 03 | Alta | Mudança de perfis e regra ampla para Python. | Configuração automática com privilégios excessivos. | `dashboard/server.py:83`; `dashboard/server.py:93` | Nenhuma mutação automática; instruções específicas, reversíveis e idempotentes. | test_dashboard_security.TLSAndNetworkTests | Nenhum comando de SO executado nas simulações. | Corrigido e validado |
| 04 | Alta | Duplo início deixava microfone ativo após parar. | Referências globais substituíam sessões pendentes. | `dashboard/static/app.html:785` | Estado e recursos por sessão; cancelamento lógico; cleanup de aquisições tardias e callbacks antigos. | dashboard_web.test.cjs; test_dashboard_browser.py | Cliques, negação, cancelamento e ciclos sem captura residual. | Corrigido e validado |
| 05 | Média | Renomeação aceitava destino fora da raiz. | Validação somente da origem. | `actions/file_controller.py:430`; `actions/file_controller.py:482` | Nome simples, destinos canônicos, reparse/identidade e rename exclusivo sem overwrite, inclusive undo. | test_file_rename_security.py | 15 aprovados; symlink real ignorado por privilégio. | Corrigido e validado |
| 06 | Média | Mensagem sumia sem confirmação. | Frontend antecipava sucesso e backend descartava sem ACK. | `dashboard/static/app.html:577`; `dashboard/server.py:427`; `main.py:2099` | ACK após aceite; id/timestamp estáveis; fila/cache limitados; erro visível e recuperação das pendências. | test_dashboard_delivery.py; test_dashboard_browser.py; test_audit_qa.py; dashboard_web.test.cjs | Ponta a ponta cifrada até Gemini simulado; falha/unknown sem replay automático. | Corrigido e validado |
| 07 | Média | Alterações concorrentes se perdiam. | Read-modify-write espalhado sem transação. | `memory/config_manager.py:15`; `memory/config_store.py:134` | Lock por caminho/thread/processo, merge transacional, fsync e replace atômico, retry Windows limitado. | test_config_transactions.py; test_setup_recovery.py | 120 processos/1800 updates no stress; corrupção e falha preservam arquivo. | Corrigido e validado |
| 08 | Média | Stream aberto escapava da limpeza. | Try/finally começava após consulta; cancelamento podia atingir escrita. | `main.py:1690` | Propriedade após open; close em todas saídas; escrita protegida contra cancelamento repetido. | test_audio_resources.py; test_runtime_shutdown.py | 5 testes de áudio; close somente após conclusão da escrita. | Corrigido e validado |
| 09 | Média | PermissionError escapava do formulário. | Gravação direta sem recuperação. | `ui.py:5127`; `ui.py:972` | Persistência central, erro seguro visível, dados/botão preservados para retry. | test_setup_recovery.py | 7 fluxos Qt aprovados, incluindo sucesso e recuperação. | Corrigido e validado |
| 10 | Média | Auto-start contornava supervisor. | Comando duplicado divergente do atalho. | `core/startup.py:18`; `ui.py:4514` | Comando único com quoting; entrada direta Windows delega ao supervisor; mutex e fechamento ordenado. | test_startup_commands.py; test_windows_launcher.py; test_runtime_shutdown.py | Atalho/registro iguais; instância existente não cria outro worker. | Corrigido e validado |
| 11 | Média | Medições vencidas pareciam atuais. | Falha global preservava GPU/temp/processos e não havia TTL. | `actions/system_monitor.py:289`; `actions/system_monitor.py:324`; `ui.py:3382` | Coleta isolada, status/timestamp/sample_id e TTL; consumidores e alertas rejeitam dados obsoletos. | test_metrics_freshness.py; test_monitor_presentation.py | 18 de frescor e 2 de apresentação aprovados. | Corrigido e validado |

As linhas da tabela referem-se à versão final local. Os testes usam arquivos
temporários e credenciais sintéticas; nenhuma chave real foi necessária.

## 3. Investigação e correções adicionais

### Correções necessárias

1. Autorização de upload antes do parsing multipart; revalidação após leituras
   assíncronas e por bloco durante downloads; limites para filas, sessões,
   comandos e tentativas de PIN; rejeição controlada de JSON/frames inválidos.
2. Encerramento explícito do servidor TLS, WebSockets, runtime e sampler.
   Fechar a janela antes de configurar a API também encerra a thread de espera.
3. Corrida Windows em `os.replace`: a suíte combinada revelou `WinError 5`.
   A causa foi reproduzida em dados sintéticos; retry transitório limitado a
   um segundo preserva a transação, e falhas permanentes continuam explícitas.
4. Removidas orientações para enfraquecer flags de segurança do navegador,
   download automático de JavaScript e efeitos de filesystem no import do painel.
5. Undo da renomeação também recusa sobrescrever uma nova entrada no nome antigo.
6. Persistência conjunta de identidade/voz/cor e dos dois dispositivos de áudio;
   falhas mantêm formulário/seleção e não alegam que os dados foram salvos.
7. Sensores independentes, rejeição de NaN/infinito, validade por amostra e
   recuperação do provedor GPU; a mesma amostra não conta três vezes num alerta.

### Defeitos encontrados pela segunda banca

- Cancelamento repetido podia fechar áudio ainda em uso. O pedido de parada
  passou a ser idempotente e a espera da escrita resiste a novos cancelamentos.
- Reload do painel perdia identidade de envio incerto e rascunho. A recuperação
  foi estendida para preservar as pendências e consultar o mesmo ID.
- Logs duplicavam argumentos, resultados, valores de memória e exceções com
  possíveis URLs autenticadas. Diagnósticos agora registram operação/estado e
  tipo de erro nos caminhos corrigidos, sem esses payloads.
- Import de plugin com módulo interno ausente não recomenda instalar um pacote
  homônimo externo; orienta atualizar a aplicação.

Refatoração geral de `ui.py`, troca do provedor de IA e alterações estéticas
foram deixadas fora do escopo por não serem necessárias às correções.

## 4. Validação e evidências

<!-- FINAL_VALIDATION -->

### Baselines que demonstraram os defeitos

- Segurança: token revogado ainda recebia 200; falha TLS ainda criava listener
  público sem SSL. Ambos falharam antes da alteração e passaram após ela.
- Web original em VM: HTTP 503 apagou o draft; duplo microfone criou duas
  sessões e apenas uma foi encerrada.
- Configuração original: duas leituras sincronizadas perderam a alteração de
  wake word. O stress posterior usou 120 processos Windows e 1.800 updates,
  em 30 rodadas, sem perda de campos.
- Áudio original: dois testes falharam porque `close()` não foi chamado após
  falha na consulta do dispositivo. O cancelamento múltiplo foi reproduzido
  novamente pela banca e corrigido em um segundo ciclo.
- Renomeação original aceitou destino absoluto fora da raiz permitida.
- Métricas originais: sete testes produziram uma aprovação, uma falha e cinco
  erros; faltavam isolamento por sensor, metadados e expiração.
- A auditoria anterior reproduziu `PermissionError` saindo do formulário de
  API e confirmou por AST o auto-start contornando o launcher.

### Escopo dos testes reais

O Chromium executa DOM, eventos, fetch e CryptoJS reais, em perfil isolado,
com todas as rotas locais e mídia simulada. A integração combinada encaminha o
payload cifrado ao ASGI real, à fila e ao relay do main; Gemini é simulado.
Os testes TLS abrem sockets reais somente em `127.0.0.1`, portas efêmeras,
usando certificados temporários e trust store restrito ao certificado de teste.
HTTP simples não retorna dados; HTTPS/WSS e encerramento são exercitados.

O smoke desktop executou `main()` e Qt no Windows, com configuração temporária
vazia: janela aberta, conversa restaurada pelo botão, estado sem conexão e
encerramento por Alt+F4. Resultado: `window_opened=true`, `window_closed=true`,
`backend_started=false`, `runtime_worker_closed=true`,
`temporary_configuration_removed=true`. Houve inspeção por Computer Use.

## 5. Segunda auditoria — papéis separados

Os revisores trabalharam em agentes separados. Os quatro primeiros produziram
pareceres próprios; o Tech Lead recebeu os quatro relatórios integrais.

### 5.1. Hacker

O revisor encontrou exposição de payloads e exceções nos diagnósticos e exigiu correção. Após a alteração, repetiu provas próprias: argumentos/resultados/erros brutos ausentes dos diagnósticos, retorno legítimo preservado. Rodou 6 testes de diagnóstico e 25 de segurança, todos aprovados. Duas sessões do mesmo dispositivo e a credencial persistente retornaram 401 após revogação. Não identificou bloqueador crítico ou alto no estado reavaliado. Limites: histórico Git completo, hardware e serviços externos não auditados.

### 5.2. Usuário confuso

<!-- CONFUSED_REVIEW -->

### 5.3. Programador sênior

O revisor reproduziu fechamento de stream durante escrita após o segundo cancelamento. Após corrigir, a prova independente com três cancelamentos retornou `close_after_each_cancel=[False,False,False]`, `close_after_write=1`, `write_finished=True`. Revalidou 5 testes de áudio, 2 de encerramento, 14 de persistência (1 skip POSIX) e 18 de métricas. Nenhum outro bloqueador estrutural confirmado. O tamanho de ui.py é dívida de manutenção, não motivo isolado para reprovação.

### 5.4. Testador

<!-- TESTER_REVIEW -->

### 5.5. Tech Lead

<!-- LEAD_REVIEW -->

## 6. Riscos residuais e limitações

- Gemini ao vivo, dispositivos físicos, reconexão real de headset, permissões
  administrativas e acesso de outro equipamento da LAN não foram exercitados.
- Um driver nativo que bloqueie indefinidamente permanece uma limitação de
  hardware: a aplicação não deve liberar um stream usado por outra thread.
- Symlink real foi ignorado por falta de privilégio no Windows; cenários
  de junction/reparse e troca de identidade foram cobertos por simulação.
- POSIX não foi executado nativamente: permissões 0600 exigem esse host;
  caminhos de renomeação exclusiva Linux/macOS foram simulados no Windows.
- Restrição de caminhos não é sandbox contra outro processo local com os
  mesmos direitos alterando ancestrais no último instante.
- Operação aceita pelo Gemini antes de revogar não pode ser recolhida.
  Resultado incerto não autoriza replay automático; deduplicação é limitada e
  a consulta antiga conserva timestamp para impedir nova execução.
- Certificados autoassinados exigem confiança explícita no dispositivo;
  certificados existentes inválidos são preservados e deixam o painel fechado.
- Inspeção somente de leitura encontrou quatro regras legadas JARVIS Dashboard
  habilitadas para qualquer perfil: portas 8000/8001 e duas regras Python.
  Foram preservadas; nenhuma regra real foi criada nesta execução. Sua remoção
  exige atribuição inequívoca ao projeto e autorização administrativa específica.
- `pynvml` e o adapter httpx/TestClient emitiram avisos de depreciação existentes;
  não houve troca de dependências de produção para suprimir os avisos.
- Não foi certificado todo o histórico Git nem feita consulta a uma base CVE.
  A varredura local de padrões e `pip check` têm alcance limitado.
- Não foi calculado percentual de cobertura; contagens são casos executados.

## 7. Arquivos alterados nesta intervenção

<!-- MODIFIED_FILES -->

Arquivos previamente alterados pelo usuário e que permaneceram intactos nesta
intervenção são distinguidos pelo manifesto do ponto de recuperação.

## 8. Alterações no ambiente e restauração

- Nenhum firewall, perfil de rede, registro, auto-start, serviço, UAC ou
  antivírus foi modificado. Os testes desses caminhos usam substitutos.
- TLS de teste usou somente loopback; sockets, browsers, tarefas e pastas
  temporárias foram encerrados/removidos pelos testes.
- Smoke UI usou somente configuração temporária e não iniciou backend nem mic.
- Ferramenta de lint Ruff 0.17.3 instalada de wheel no PyPI oficial, sem scripts
  de build, em `../.codex-tools/audit/`, separada do runtime. Nenhuma dependência
  da aplicação foi adicionada ou atualizada. O ambiente de ferramenta e o ponto
  de recuperação foram mantidos para reprodução, fora do repositório Git.
- Nenhum conteúdo do projeto, log sensível ou credencial foi enviado a serviço
  externo. Somente a ferramenta pública de desenvolvimento foi baixada.

## 9. Veredito final

<!-- FINAL_VERDICT -->
