# Site_barbearia-

Sistema de Fila para Barbearia — Guild Tech Support


Este sistema implementa uma solução robusta e transacional de fila virtual em tempo real para uma única barbearia com múltiplas cadeiras (onde cada barbeiro cadastrado corresponde a uma cadeira de atendimento).

---

## 1. Visão Geral e Princípios Arquiteturais

- **Interface integrada**: templates Django, CSS e JavaScript sem framework adicional, conectados às APIs por sessão e CSRF; manifesto e service worker instaláveis.
- **Fila, Não Agenda**: Não há agendamento por data ou horário; trata-se estritamente de uma fila por ordem de chegada.
- **Dados temporários**: Sem cadastros permanentes de clientes, sem senhas de clientes e sem histórico de cortes persistido. Ao finalizar o atendimento ou sair da fila, os dados do participante são excluídos do banco de dados ativo.
- **Identificação Temporária Segura**: O cliente recebe uma credencial de alta entropia transmitida exclusivamente via cookie `HttpOnly` com proteção CSRF e SameSite. O banco armazena unicamente o hash SHA-256 do token.
- **SQLite Concorrente**: Transações com modo `BEGIN IMMEDIATE`, WAL opcional e verificado (`SQLITE_ENABLE_WAL=True`), retentativas automáticas defensivas e integridade garantida por Constraints estritas de banco de dados.

---

## 2. Requisitos e Dependências

- **Python**: 3.12+ (compatível com 3.14)
- **Django**: 5.2+ (com suporte nativo a transações IMMEDIATE no backend SQLite)
- **tzdata**: 2026.5+
- **python-dotenv**: 1.2+
- **asgiref** e **sqlparse**

---

## 3. Instalação e Configuração

### 3.1. Criar e Ativar o Ambiente Virtual

No diretório `barbearia/`:

```bash
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3.2. Instalar as Dependências

```bash
pip install -r requirements.txt
```

### 3.3. Configurar Variáveis de Ambiente

Copie o arquivo `.env.example` para `.env`:

```bash
cp .env.example .env
```

Parâmetros disponíveis no `.env`:

| Variável | Padrão | Descrição |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `configuracao.settings.dev` | Módulo de configuração (`dev` ou `prod`) |
| `DJANGO_SECRET_KEY` | *(chave interna de dev)* | Chave criptográfica única e secreta em produção |
| `DJANGO_DEBUG` | `False` | Ativação do modo debug (sempre `False` em produção) |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Lista separada por vírgula de domínios permitidos |
| `SQLITE_TIMEOUT` | `20` | Timeout em segundos para contenção no SQLite |
| `SQLITE_ENABLE_WAL` | `False` | Habilita Write-Ahead Logging no SQLite |
| `RATE_LIMIT_LOGIN_MAX_TENTATIVAS` | `30` | Limite de POSTs de autenticação por IP |
| `RATE_LIMIT_LOGIN_JANELA_MINUTOS` | `10` | Janela de contagem de tentativas |
| `RATE_LIMIT_LOGIN_BLOQUEIO_MINUTOS` | `30` | Duração do bloqueio após atingir o limite |
| `TRUSTED_PROXY_IPS` | `""` | Lista de IPs de proxies confiáveis para extração de IP real |

### 3.4. Executar as Migrações do Banco de Dados

```bash
python manage.py migrate
```

---

## 4. Criação de Contas e Administração

### 4.1. Criar a Conta do Proprietário (Administrador)

O sistema conta com um comando administrativo exclusivo para inicializar a conta do proprietário:

```bash
python manage.py criar_proprietario
```

A conta do proprietário é criada como superusuário ativo (`is_staff=True`, `is_superuser=True`).

### 4.2. Cadastrar Barbeiros

O cadastro de novos barbeiros é realizado pelo proprietário:
- Via endpoint administrativo: `POST /api/proprietario/barbeiros/cadastrar/`
- Ou pelo painel nativo do Django (`/admin/`) autenticado como superusuário.

As contas de barbeiros são criadas com privilégios restritos: `is_staff=False`, `is_superuser=False`.

---

## 5. Execução do Sistema

O backend foi arquitetado para execução em servidor único com dois processos desacoplados:

### 5.1. Processo 1 — Servidor Web HTTP

Em desenvolvimento:
```bash
python manage.py runserver
```

Em produção:
Utilize um servidor WSGI/ASGI de nível de produção (Gunicorn / Uvicorn):
```bash
gunicorn configuracao.wsgi:application --bind 127.0.0.1:8000 --workers 1
```

### 5.2. Processo 2 — Worker Supervisor de Chamadas e Faltas

O processamento das chamadas de 30 segundos e do avanço automático das filas ocorre continuamente em segundo plano, sem depender de interações ou requisições HTTP:

```bash
python manage.py processar_filas --intervalo 1.0
```

Para executar um único ciclo (diagnóstico ou testes):
```bash
python manage.py processar_filas --once
```

> **Supervisão em Produção (Systemd / Supervisord)**:  
> Execute o comando `processar_filas` sob um gerenciador de processos como `systemd` ou `supervisor` para garantir reinício automático em caso de falha do sistema operacional.

### 5.3. Processo 3 — Limpeza Periódica de Registros Técnicos de Segurança

Para remover registros de tentativas de login expirados sem interferir em bloqueios vigentes (Passo 19):

```bash
python manage.py limpar_registros_seguranca
```

Recomenda-se configurar no `cron` do servidor para execução horária ou diária.

---

## 6. Regras de Negócio Fundamentais

### 6.1. Ciclo da Chamada de 30 Segundos
1. Quando a cadeira do barbeiro está **ativa**, **online**, sem corte em andamento (`EM_ATENDIMENTO`) e sem ninguém chamado (`CHAMADO`), o próximo cliente com `AGUARDANDO` e `aguardando_ultrapassagem=False` é **automaticamente chamado**.
2. É gerado um `identificador_chamada` único (UUID) e definido o prazo de vencimento para exatamente **30 segundos** após o início da chamada, baseado no relógio do servidor.
3. Se o barbeiro confirmar a presença com "Iniciar Atendimento":
   - O prazo de 30 segundos e o UUID do ciclo são revalidados;
   - O cliente passa para `EM_ATENDIMENTO`;
   - Os campos da chamada são limpos;
   - A condição de falta daquele cliente é desativada.
4. Se o prazo de 30 segundos expirar sem confirmação:
   - O participante retorna para `AGUARDANDO`;
   - Recebe a marcação `aguardando_ultrapassagem = True`;
   - É movido para o **final da fila** (recebe nova `ordem_fila` da cadeira);
   - O contador de faltas **não** é incrementado na expiração;
   - O próximo cliente elegível é chamado imediatamente.

### 6.2. Regra das 5 Ultrapassagens por Falta
Adota-se a seguinte definição estrita:
- Uma **ultrapassagem** ocorre quando **outro cliente** da mesma cadeira inicia efetivamente `EM_ATENDIMENTO` enquanto um cliente ausente está com `aguardando_ultrapassagem = True`.
- Ao iniciar efetivamente um atendimento:
  1. O sistema localiza todas as outras participações daquela cadeira com `aguardando_ultrapassagem = True`;
  2. Incrementa `ultrapassagens_por_falta += 1` em cada uma delas;
  3. Se atingir **5**, o participante é **imediatamente excluído** do sistema e seus dados são apagados;
  4. Se estiver abaixo de 5, `aguardando_ultrapassagem` volta a ser `False`, dando ao cliente uma nova oportunidade de chamada automática assim que a cadeira liberar.

### 6.3. Desligar a Cadeira (Ficar Offline)
- Ação explícita do barbeiro;
- Impede imediatamente novas entradas;
- Exclui todas as participações com status `AGUARDANDO` e `CHAMADO`;
- Se houver corte em andamento (`EM_ATENDIMENTO`), ele é preservado para conclusão normal e excluído ao finalizar;
- A cadeira permanece visível na Home pública com status `online = False` e `aceita_entrada = False`.

### 6.4. Rate Limiting de Login por IP
- Aplicado exclusivamente nos endpoints de POST de autenticação (`/api/barbeiro/auth/login/` e `/api/proprietario/auth/login/`);
- Janela de **10 minutos**, limite de **30 tentativas POST** por IP;
- A 30ª tentativa ativa bloqueio de **30 minutos** para as tentativas subsequentes;
- Retorna HTTP `429 Too Many Requests` com o cabeçalho `Retry-After: <segundos>`;
- Requisições recebidas durante o bloqueio não prolongam o tempo de expiração;
- Contagem persistente e atômica no SQLite;
- Não afeta a Home pública, o acompanhamento de clientes nem as requisições autenticadas.

---

## 7. Contratos da API (Mobile-First)

Todas as rotas privadas retornam o cabeçalho `Cache-Control: no-store, no-cache, must-revalidate`.

### 7.1. Endpoints Públicos

#### `GET /api/publico/cadeiras/`
Retorna todas as cadeiras cadastradas sem expor dados pessoais de clientes.
- **Resposta 200 OK**:
  ```json
  {
    "horario_servidor": "2026-10-03T21:00:00-03:00",
    "cadeiras": [
      {
        "identificador_barbeiro": 1,
        "nome_exibicao": "Cadeira 1 - Bruno",
        "ativo": true,
        "online": true,
        "aceita_entrada": true,
        "quantidade_aguardando": 3,
        "atendimento_em_andamento": true
      }
    ]
  }
  ```

#### `POST /api/publico/cadeiras/<barbeiro_id>/entrar/`
Cadastra participação temporária.
- **Corpo da Requisição (JSON)**:
  ```json
  {
    "primeiro_nome": "Lucas",
    "sobrenome": "Mendes",
    "telefone": "(11) 98765-4321",
    "data_nascimento": "1995-04-10"
  }
  ```
- **Resposta 201 Created** (ou 200 OK se reapresentado):
  - Cookie `token_acesso_fila` definido como HttpOnly e SameSite=Lax.
  ```json
  {
    "mensagem": "Participação registrada com sucesso.",
    "identificador_publico": "c3d4e5f6-7890-4123-8abc-def012345678",
    "ordem_fila": 1,
    "status": "CHAMADO"
  }
  ```
- **Erros**: 400 (validação), 409 (cadeira offline ou telefone duplicado).

---

### 7.2. Endpoints do Cliente Temporário

#### `GET /api/cliente/acompanhamento/`
Consulta a participação ativa do cliente via cookie HttpOnly.
- **Resposta 200 OK (Ativa)**:
  ```json
  {
    "status": "CHAMADO",
    "identificador_publico": "c3d4e5f6-7890-4123-8abc-def012345678",
    "ordem_fila": 1,
    "clientes_na_frente": 0,
    "identificador_chamada": "a1b2c3d4-e5f6-4789-8012-3456789abcde",
    "chamada_iniciada_em": "2026-10-03T21:00:00-03:00",
    "chamada_expira_em": "2026-10-03T21:00:30-03:00",
    "horario_servidor": "2026-10-03T21:00:05-03:00",
    "tempo_restante_segundos": 25,
    "barbeiro": {
      "nome_exibicao": "Cadeira 1 - Bruno",
      "online": true
    }
  }
  ```
- **Resposta 200 OK (Encerrada)**:
  Se o cliente foi finalizado ou removido, limpa o cookie:
  ```json
  {
    "status": "ENCERRADO",
    "mensagem": "Participação encerrada.",
    "horario_servidor": "2026-10-03T21:01:00-03:00"
  }
  ```

#### `POST /api/cliente/sair/`
Permite ao próprio cliente sair voluntariamente da fila (em `AGUARDANDO` ou `CHAMADO`).
- **Resposta 200 OK**:
  ```json
  { "mensagem": "Saída da fila realizada com sucesso." }
  ```
- **Erro**: 409 se estiver em `EM_ATENDIMENTO`.

---

### 7.3. Endpoints do Barbeiro

- `POST /api/barbeiro/auth/login/`: Autentica barbeiro com rate limit por IP.
- `POST /api/barbeiro/auth/logout/`: Encerra a sessão.
- `GET /api/barbeiro/painel/`: Retorna dados operacionais da cadeira e lista de participantes com nome completo, telefone normalizado, idade calculada, horário de entrada e status.
- `POST /api/barbeiro/cadeira/status/`: Altera estado online/offline (`{"online": true/false}`).
- `POST /api/barbeiro/fila/chamar-proximo/`: Chama o próximo elegível na ordem.
- `POST /api/barbeiro/fila/chamar-especifico/<participacao_id>/`: Chama um cliente escolhido em espera. Se havia outro chamado vigente, retorna o anterior à espera sem penalidade.
- `POST /api/barbeiro/fila/iniciar-atendimento/<participacao_id>/`:
  - Entrada: `{"identificador_chamada": "<uuid>"}`
  - Confirma presença e inicia corte.
- `POST /api/barbeiro/fila/finalizar-corte/<participacao_id>/`: Conclui o corte, apaga os dados do participante e chama o próximo se a cadeira continuar online.
- `POST /api/barbeiro/fila/remover/<participacao_id>/`: Remove participante em espera ou chamado.

---

### 7.4. Endpoints do Proprietário

- `POST /api/proprietario/auth/login/`: Login administrativo isolado.
- `POST /api/proprietario/auth/logout/`: Logout administrativo.
- `GET /api/proprietario/visao-geral/`: Resumo executivo de todas as cadeiras e estatísticas.
- `GET /api/proprietario/barbeiros/`: Lista todos os barbeiros cadastrados.
- `POST /api/proprietario/barbeiros/cadastrar/`: Cadastra barbeiro (`username`, `senha`, `nome_exibicao`).
- `POST /api/proprietario/barbeiros/<id>/editar/`: Edita nome de exibição.
- `POST /api/proprietario/barbeiros/<id>/redefinir-senha/`: Redefine a senha do barbeiro.
- `POST /api/proprietario/barbeiros/<id>/status/`: Ativa ou desativa a cadeira (`{"ativo": true/false}`).
- `GET /api/proprietario/filas/`: Consulta todas as participações ativas da barbearia.
- `POST /api/proprietario/filas/remover/<participacao_id>/`: Remove qualquer participante em espera ou chamado.

---

## 8. Contrato e Limitações do Alerta Sonoro no Navegador

O endpoint `/api/cliente/acompanhamento/` fornece `tempo_restante_segundos`, `horario_servidor`, `chamada_iniciada_em` e `chamada_expira_em`.

### Funcionamento da interface:
- O frontend emite avisos sonoros no navegador após o toque em “Ativar alerta sonoro” enquanto o cliente estiver no status `CHAMADO` e `tempo_restante_segundos > 0`;
- O alerta sonoro para imediatamente quando o status mudar (para `EM_ATENDIMENTO` ou `ENCERRADO`), a chamada expirar ou a cadeira ficar offline.

### Limitações Técnicas Reais dos Navegadores:
- **Autoplay Policy**: Navegadores modernos (Chrome, Safari, Firefox) bloqueiam reprodução automática de áudio sem interação prévia do usuário na página (clique/toque);
- **Abas em Segundo Plano / Telas Bloqueadas**: A execução de áudio pode ser suspensa ou limitada pelo sistema operacional móvel quando a tela do celular é bloqueada ou o navegador é minimizado;
- O toque sonoro não comprova presença; somente o barbeiro confirma o início do atendimento no painel presencial.

---

## 9. Backup e Restauração Consistente do SQLite (WAL Mode)

Quando o modo Write-Ahead Logging (WAL) está ativo, o banco de dados é composto por até 3 arquivos:
- `db.sqlite3`
- `db.sqlite3-wal`
- `db.sqlite3-shm`

> **CUIDADO**: Nunca copie apenas o arquivo `db.sqlite3` enquanto a aplicação estiver em execução.

### Procedimento Seguro de Backup:

Utilize a API nativa de backup do SQLite ou a ferramenta `sqlite3` CLI:

```bash
# Backup consistente em execução (cria cópia sem inconsistências)
sqlite3 db.sqlite3 ".backup 'backup_barbearia_$(date +%Y%m%d_%H%M%S).sqlite3'"
```

### Procedimento de Restauração:

1. Pare o processo web e o worker `processar_filas`;
2. Substitua o arquivo `db.sqlite3` pelo arquivo de backup;
3. Exclua quaisquer arquivos residuais `db.sqlite3-wal` e `db.sqlite3-shm`;
4. Reinicie os serviços.

---

## 10. Testes Automatizados

A suíte executada contém **69 testes** de regras de negócio, banco, concorrência, autenticação, privacidade e contratos da interface. Testes não constituem garantia de ausência de defeitos.

Para executar todos os testes:

```bash
python manage.py test fila
```

### Cobertura dos Testes:
- **Modelos e Constraints (`test_models.py`)**: Validações de nomes Unicode, normalização de telefone brasileiro (10/11 dígitos com validação de DDDs e celular iniciando com 9), cálculo dinâmico de idade antes e depois do aniversário, unicidade de telefone, ordem por cadeira, limites de status e restrições estritas de datas.
- **Regras de Negócio e Serviços (`test_services.py`)**: Entrada, chamadas automáticas de 30s, expiração, regra das 5 faltas acumuladas com exclusão na 5ª, reapresentação de token, autonomia do barbeiro, saída do cliente e desligamento da cadeira.
- **Rate Limiting por IP (`test_rate_limit.py`)**: 30 tentativas permitidas, bloqueio na subsequente com HTTP 429 e `Retry-After`, independência de rotas públicas e identificação segura por proxy confiável.
- **Concorrência e Transações SQLite (`test_concorrencia.py`)**: Requisições simultâneas em threads separadas, corridas de início de atendimento, corridas de entrada com mesmo telefone e incremento estrito de ultrapassagens.
- **Worker e Comandos Administrativos (`test_worker.py`)**: Execução única (`--once`), reinício sem fabricação retroativa de faltas e limpeza de registros técnicos expirados.


## 11. Interface e PWA concluídos

| Tela | Rota |
|---|---|
| Cadeiras públicas | `/` |
| Cadastro temporário | `/cadastro/<id>/` |
| Minha fila e alerta sonoro | `/minha-fila/` |
| Login do barbeiro | `/acesso/barbeiro/` |
| Minha cadeira e perfil | `/barbeiro/`, `/barbeiro/perfil/` |
| Login do proprietário | `/acesso/gestao/` |
| Visão geral, equipe e filas | `/gestao/`, `/gestao/equipe/`, `/gestao/clientes/` |
| Cadastro e edição do profissional | `/gestao/novo-barbeiro/`, `/gestao/barbeiro/<id>/` |

O cabeçalho e os retratos ilustrativos de Rafael/Marcos usam a referência enviada, enquadrada por CSS. Outros profissionais recebem iniciais, sem inventar fotografias. Textos, campos, botões, contagens e navegação são elementos reais. Os títulos usam Bodoni Moda, distribuída localmente com licença OFL; a referência raster não fornece sua fonte original, portanto não há garantia de identidade pixel a pixel. O aplicativo não desenha a moldura de iPhone nem uma barra de status fictícia.

“Já estou na fila” reapresenta somente a participação autorizada pelo cookie deste navegador. “Clientes na fila” mostra apenas registros temporários ativos; não existe base permanente de clientes, mesmo que a referência use a expressão “Clientes cadastrados”.

### Precedência das especificações

O prompt detalhado anexado por último prevalece nas regras divergentes: exclusão na **quinta ultrapassagem efetiva**; ao ficar offline, exclusão de espera/chamada e preservação do corte em andamento até sua finalização. O frontend foi implementado por solicitação explícita desta etapa, que substitui a restrição “somente backend” da etapa anterior.

### Iniciar no Windows

No diretório `barbearia`, após instalar as dependências:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py criar_proprietario
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Em outro terminal, no mesmo diretório:

```powershell
.\.venv\Scripts\python.exe manage.py processar_filas
```

Abra `http://127.0.0.1:8000/acesso/gestao/`, entre com o proprietário criado e cadastre os profissionais. Cada barbeiro entra em `/acesso/barbeiro/` e liga sua cadeira. Nenhuma credencial padrão ou cliente fictício foi inserido no banco real. Sair da sessão não desliga a cadeira.

O worker é obrigatório e deve ficar supervisionado. Sem ele, a API ainda rejeita confirmações vencidas, mas a progressão automática sem requisições não ocorre. Em produção use um processo web, um worker e o mesmo banco local persistente. Exemplo systemd para o worker (adapte os caminhos):

```ini
[Unit]
Description=Processador de filas da barbearia
After=network.target
[Service]
User=barbearia
WorkingDirectory=/srv/barbearia
Environment=DJANGO_SETTINGS_MODULE=configuracao.settings.prod
EnvironmentFile=/srv/barbearia/.env
ExecStart=/srv/barbearia/.venv/bin/python manage.py processar_filas
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
```

### Produção e instalação PWA

- Configure `DJANGO_SETTINGS_MODULE=configuracao.settings.prod`, chave secreta externa de pelo menos 50 caracteres e hosts explícitos. A configuração recusa chave padrão e hosts ausentes.
- Instale o servidor WSGI escolhido antes do comando de produção (ex.: `pip install gunicorn` em Linux). Execute `python manage.py collectstatic --noinput` e sirva **somente** `staticfiles/` em `/static/` pelo servidor HTTP; nunca sirva a pasta do banco ou do projeto.
- Termine HTTPS no servidor web. Preserve a identificação de conexão segura; configure cabeçalhos de proxy somente para seu proxy real, que deve sobrescrever os cabeçalhos enviados pelos clientes. Não confie em `X-Forwarded-For` arbitrário.
- Execute `python manage.py check --deploy --settings=configuracao.settings.prod` com as variáveis reais antes de publicar. Não houve deploy externo nesta entrega.
- Instalação e service worker exigem HTTPS, exceto localhost. No Android use “Adicionar à tela inicial”; no Safari/iPhone use Compartilhar → Adicionar à Tela de Início. A oferta depende do navegador.
- O service worker guarda apenas CSS, JavaScript, fontes, imagens públicas e uma página offline genérica. APIs, formulários, sessões e dados pessoais **não** são armazenados em cache. A fila exige conexão e não aceita operações offline.
- Polling: 5 segundos na home, 2 segundos nos painéis visíveis. O servidor decide todos os prazos; o navegador só apresenta a contagem e o som, limitado ao tempo restante do ciclo atual. A confirmação de presença permanece exclusiva do barbeiro.
- O editor genérico `/admin/` foi desabilitado para evitar alterações que contornem os serviços transacionais e registros de auditoria com nomes de participantes. Use a gestão dedicada.
- Um IP pode representar uma rede compartilhada: as 30 tentativas podem ser consumidas por várias pessoas dessa rede.

### Privacidade dos backups

Apagar o registro do banco ativo não apaga cópias de backup anteriores nem equivale à sobrescrita forense do disco. Backups capturados enquanto havia clientes na fila podem conter seus dados; acesso, descarte e proteção dessas cópias devem ser administrados pelo responsável, sem política legal de retenção inventada pelo aplicativo.

### Validação desta entrega

- `python manage.py test --noinput`: 67 testes aprovados; após melhoria do comando inicial, os 16 testes de interface/comando foram reexecutados e passaram (69 testes distintos no total), com SQLite em arquivo e conexões independentes nos testes concorrentes.
- Regressões adicionais: início atômico com UUID vigente, troca de chamada após expiração, cadeira obsoleta/offline, posição incluindo chamado, tipos JSON, CSRF, páginas protegidas e manifesto.
- Fluxo real no Edge automatizado: cadastro → chamada → início → finalização → exclusão; gestão de profissionais; desligamento da cadeira; página offline. Banco de validação separado do banco real.
- Capturas revisadas de home, cadastro, login, acompanhamento, cadeira e gestão. Verificações de layout em 320, 360, 432 e 1280 pixels.
- A reprodução sonora foi ativada e o contrato verificado; volume audível em aparelhos físicos e instalação em iPhone/Android precisam ser conferidos nesses aparelhos.
