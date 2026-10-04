# Backend SaaS — Guild Tech Support

Esta cópia é `C:\Users\andre\Documents\SaaS_Barbeariamodel`. O aplicativo single tenant em outra pasta não faz parte desta implementação. Não houve deploy nem cobrança real. HTML/CSS operacionais foram preservados como arquivos; as rotas de páginas operacionais oferecem contratos JSON mínimos enquanto o novo visual não é fornecido.

## Arquitetura e dados

Django 5.2.17, SQLite compartilhado, transações IMMEDIATE. `fila` mantém as regras de chamada, espera, ultrapassagem, exclusão e expediente. `saas` cuida de identidade permanente, estabelecimentos, contratação, direitos de acesso e administração GTS.

| Entidade | Responsabilidade |
|---|---|
| Barbearia | UUID estável, slug único, nome, contato opcional, provisionamento, bloqueio com motivo e diagnóstico de integração |
| VinculoBarbearia | Usuário permanente com papel DONO; um dono ativo por empresa. O modelo permite vínculos com várias empresas; o onboarding MVP cria apenas uma |
| Barbeiro | Conta operacional existente, uma cadeira e tenant obrigatório. Fonte única da permissão de barbeiro; não duplica papel em VinculoBarbearia |
| ParticipacaoFila | Dados temporários e tenant obrigatório; telefone único **por empresa**, ordem/status por cadeira |
| Plano | Um plano mensal BRL 10000 centavos, Price por ambiente, inicialmente indisponível até configurar |
| ClienteCobranca | Customer por empresa/ambiente e chave idempotente persistida |
| Assinatura | Histórico separado de cada Subscription, status original, períodos, pagamento confirmado, cancelamento e sincronização |
| TentativaContratacao | Reserva durável, chave idempotente, sessão, expiração, situação. Uma tentativa aberta por empresa/ambiente |
| EventoStripe | ID único por ambiente, tipo, ID do objeto, tentativas, erro sanitizado, datas e estado. Sem payload completo/cartão |
| AcaoPlataforma | Autor e motivo de bloqueios/sincronizações/reprocessamentos administrativos |

O tenant vem do slug resolvido no servidor. APIs do dono exigem vínculo ativo com esse tenant; `/conta/` resolve o vínculo autenticado e ignora IDs de empresa enviados no corpo. Não existe fallback para uma empresa padrão. Rotas operacionais globais antigas deixam de existir.

Google identifica por `SocialAccount(provider='google', uid=<sub>)`, não por e-mail. Google não transforma barbeiro nem administrador em dono. O dono não recebe `is_staff` nem `is_superuser`. Contas operacionais continuam com usuário/senha. Administradores GTS usam conta separada e o admin privado existente. O antigo comando `criar_proprietario` agora é um alias de criação de administrador GTS; prefira `criar_admin_gts`.

Não há fonte global de configurações operacionais no modelo legado. As configurações atuais da cadeira e a sequência da fila permanecem por Barbeiro, agora associado ao tenant. Fotos, privacidade dos clientes e remoção ao finalizar foram preservadas.

## Migração e checkpoint

Checkpoint inicial: commit `33af28e`, incluindo as alterações locais anteriores à conversão. Backup consistente via `sqlite3.Connection.backup` em `.verificacao/pre-saas/db.sqlite3`; fotos em `.verificacao/pre-saas/media/`. Esses arquivos são locais, ignorados pelo Git e podem conter dados pessoais. Proteja-os como o banco original.

Migrações:

1. `saas.0001`: estruturas permanentes.
2. `fila.0003`: campos de tenant inicialmente opcionais e unicidade composta de telefone.
3. `fila.0004`: cria `barbearia-inicial` **somente se houver operação legada**, preenche vínculos, valida relações e torna campos obrigatórios. As cadeiras migradas ficam offline. Cria o plano indisponível; não cria assinatura/dono comercial.
4. `fila.0005`: triggers SQLite garantem que participação e cadeira pertençam à mesma empresa, inclusive em atualizações diretas; impedem transferência de cadeira entre empresas.
5. Migrações padrão do allauth para identidades Google.

O superusuário legado continua administrador GTS. Após o dono efetuar seu primeiro login Google, associe explicitamente a conta à empresa migrada **antes de ele iniciar o cadastro de outra empresa**:

```powershell
.\.venv\Scripts\python.exe manage.py vincular_dono_migrado --usuario-id ID --slug barbearia-inicial
```

Isso não libera pagamento. A empresa contrata normalmente. Banco vazio não recebe barbearia demonstrativa. Nenhuma migration anterior foi apagada; nenhum banco foi reinicializado. Para voltar à versão inicial, pare web/workers, restaure código do checkpoint e o backup SQLite consistente com suas fotos. Não faça rollback parcial das migrations com dados multitenant: a unicidade global antiga pode conflitar com telefones repetidos entre empresas.

## Configuração local

No diretório `barbearia`, instale `requirements.txt`, configure `.env`, execute migrations e os processos:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
# Em terminais separados:
.\.venv\Scripts\python.exe manage.py processar_filas --intervalo 1
.\.venv\Scripts\python.exe manage.py processar_cobranca --intervalo 5
# Agendar periodicamente (por exemplo, a cada minuto):
.\.venv\Scripts\python.exe manage.py reconciliar_saas --limite 25
```

Supervisione ambos os workers. `--once` permite executar um ciclo. O reconciliador visita um lote das empresas menos recentemente verificadas; chamadas de integração possuem timeout, paginação e orçamento de 100 requisições por cliente de integração. O polling operacional nunca chama a Stripe. Eventos falhos têm até oito tentativas, backoff limitado a uma hora e reprocessamento explícito disponível. Eventos PROCESSANDO abandonados voltam a ser elegíveis após dez minutos; erro no último intento permanece visível para reprocessamento administrativo.

SQLite é mantido: use banco em disco local persistente, servidor único, um worker de cada tipo, transações curtas e poucos processos web. SQLite serializa escritas; múltiplas empresas aumentam contenção. Não distribua o arquivo por rede nem use réplicas de app com discos diferentes. Escalar para vários hosts exige uma mudança planejada de banco/coordenação; isso não foi feito silenciosamente nesta etapa.

## Variáveis novas

| Variável | Uso |
|---|---|
| `SAAS_BASE_URL` | Origem pública confiável dos retornos Stripe; local `http://127.0.0.1:8000`, produção HTTPS |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth Client Web do ambiente |
| `STRIPE_AMBIENTE` | `test` por padrão; `live` somente com configuração deliberada de produção |
| `STRIPE_SECRET_KEY` | Chave secreta do ambiente; nunca no frontend |
| `STRIPE_WEBHOOK_SECRET` | Segredo do endpoint do ambiente; o segredo da CLI é diferente do Dashboard |
| `STRIPE_PORTAL_CONFIGURATION_ID` | Configuração do Portal validada no backend |
| `SAAS_TOLERANCIA_DIAS` | 3 por padrão; ancorado no fim do período realmente pago |

Continuam as variáveis Django, SQLite, media, proxy e admin privado existentes. `.env` não é versionado. Não misture chaves, dados, URLs e segredos de produção e teste. O banco contém coluna de ambiente nos vínculos de cobrança; use também bancos/deploys separados por ambiente operacional.

## Google OAuth

Biblioteca `django-allauth[socialaccount]==65.19.7`, fluxo OAuth suportado com PKCE, sessão e CSRF. Cadastre OAuth Client tipo Web no Google e callback exato `http://127.0.0.1:8000/accounts/google/login/callback/` no ambiente local; em produção troque pela origem HTTPS real. Configure tela de consentimento e usuários de teste conforme a configuração do projeto Google.

Escopos: somente `openid`, `email`, `profile`. Login começa por POST com CSRF em `/accounts/google/login/`; a página mínima `/` já contém o formulário. Tokens Google não são persistidos. Associação e autenticação automáticas por e-mail estão desabilitadas; conexão de uma conta operacional/administrativa ao Google comercial é recusada. A validação do provedor, callback, token e estado é feita pela biblioteca. Dados enviados pelo frontend não são aceitos como prova de identidade.

Referências: [Google no allauth](https://docs.allauth.org/en/latest/socialaccount/providers/google.html) e [configuração de contas sociais](https://docs.allauth.org/en/latest/socialaccount/configuration.html).

## Stripe e contratação

SDK fixado `stripe==16.0.0`, API fixada `2026-09-30.endive`. Configurar o endpoint de webhook para a mesma versão. A integração usa períodos nos **itens** da assinatura, a relação de assinatura em `invoice.parent.subscription_details`, preço em `line.pricing.price_details` e `invoice_payments` com PaymentIntent confirmado. A fronteira converte `StripeObject.to_dict()`, pois o SDK 16 não implementa os métodos de `dict`. Os nomes foram conferidos no SDK instalado, além das [referências oficiais de versão](https://github.com/stripe/stripe-python) e [faturas](https://docs.stripe.com/api/invoices).

Com chave **de teste** configurada:

```powershell
.\.venv\Scripts\python.exe manage.py configurar_stripe
```

O comando cria/reutiliza produto e Price: BRL, 10000 centavos, um mês, quantidade 1; grava o Price autorizado em Plano e imprime o ID de configuração do Portal para o `.env`. Aceita `--price-id price_...` para validar um preço já criado. Em ambiente live não cria produto/preço/Portal automaticamente: exige Price existente e configuração externa. Não executamos esse comando contra uma conta Stripe nesta entrega.

Fluxo:

1. Dono autentica com Google e consulta `/conta/`.
2. Cadastra nome, slug público e contato opcional; reserva slug e vínculo de dono em transação.
3. POST `/conta/checkout/` valida dono, plano, Customer e contratações em aberto. Não usa valor/Price/Customer enviados pelo navegador.
4. Checkout hospedado `mode=subscription`, somente cartão, sem trial, cupom, promoção, plano anual ou cobrança por profissional.
5. Reserva local e chaves persistidas tornam retries idempotentes. Duas abas compartilham a tentativa ou recebem `INTEGRACAO_EM_ANDAMENTO`; rede nunca ocorre dentro de transação SQL.
6. Retorno do navegador só consulta estado local. Conclusão do Checkout ou criação de Subscription isoladamente não liberam acesso.
7. Webhook valida assinatura no corpo bruto e ambiente; persiste apenas IDs/campos técnicos e confirma recebimento.
8. Worker consulta Stripe vigente, valida vínculo, preço, Customer, ambiente, pagamento e período. Pago fora da Stripe sem PaymentIntent confirmado não concede direito.
9. Provisionamento retoma idempotentemente; sem equipe, estado EQUIPE. Com equipe ativa, PRONTO. Não cria barbeiros nem liga cadeiras automaticamente.

Metadata contém apenas IDs internos de barbearia/tentativa, nunca nomes/telefones/dados da fila. Não usa Connect, split ou repasses.

### Webhooks

Endpoint POST `/stripe/webhook/`, exclusivamente ele dispensa CSRF e exige assinatura Stripe válida. Assine:

- `checkout.session.completed`, `checkout.session.expired`;
- `customer.subscription.created`, `.updated`, `.deleted`;
- `invoice.paid`, `.payment_failed`, `.payment_action_required`, `.finalization_failed`;
- `charge.refunded`, `charge.dispute.created`, `.updated`, `.closed`.

Métodos assíncronos não estão habilitados. Reembolsos/disputas vão para ANALISE administrativa, sem confundir com cancelamento ou reembolso automático. Eventos duplicados são deduplicados e eventos antigos consultam o estado vigente, em vez de sobrescrever cegamente o estado local. Falha de persistência não retorna sucesso. Falha de processamento permanece no banco após reinício.

Exemplo externo de teste (Stripe CLI instalada e autenticada na conta de teste):

```text
stripe listen --forward-to http://127.0.0.1:8000/stripe/webhook/
```

Configure o `whsec_...` mostrado pela CLI; faça uma contratação no Checkout com cartão de teste. Eventos sintéticos `stripe trigger` sem vínculos locais válidos são rejeitados pelo processamento; não simulam por si só uma contratação completa do app. Para Test Clocks, use Customer de teste corretamente vinculado à empresa e avance renovações/atrasos na Stripe, observando o relógio UTC da aplicação para avaliar os limites locais.

### Portal, cancelamento e recontratação

POST `/conta/portal/` cria sessão somente para Customer do dono. A configuração permite método de pagamento, documentos e cancelamento ao final do período; troca de plano fica desabilitada. Configuração fora dessa política é recusada. Retorno do Portal não libera acesso.

POST `/conta/cancelar/` agenda cancelamento; POST `/conta/retomar/` desfaz enquanto elegível. Mantém acesso até o período pago; assinatura encerrada suspende. Recontratação preserva empresa, equipe e histórico e cria outra Assinatura. Cancelamento não apaga conta nem emite reembolso. Documentos Stripe não são tratados como nota fiscal brasileira; emissão fiscal está fora desta etapa.

## Acesso e suspensão

`saas.acesso` centraliza a decisão. Pagamento inicial não confirmado nunca recebe acesso. Renovação `past_due` tem três dias após `acesso_pago_ate`; novas falhas não reiniciam o prazo. Assinatura `unpaid`, encerrada, bloqueio administrativo ou provisionamento incompleto impedem operação. Períodos vêm da cobrança Stripe, sem soma fixa de 30 dias. Se Stripe ficar indisponível, mantém apenas direitos já confirmados até seus limites locais.

Na suspensão, novas entradas, abertura de expediente, chamadas e início de corte são negados. Consulta, remoção da espera e finalização de corte já iniciado continuam autorizados. Worker/sincronização desligam cadeiras **sem executar a exclusão da fila própria do desligamento manual**. A conta do dono e cobrança continuam acessíveis. Regularização não recria participações nem liga cadeiras. Bloqueio administrativo prevalece sobre qualquer pagamento.

## Rotas para o futuro frontend

| Rota | Método / finalidade |
|---|---|
| `/` | GET, página mínima GTS e início do Google |
| `/conta/`, `/conta/retorno/` | GET, estado atual, onboarding, períodos, ações e situação de integração |
| `/conta/barbearia/` | POST JSON `{nome, slug, contato_comercial?}` |
| `/conta/checkout/` | POST, retorna URL Checkout/estado |
| `/conta/portal/` | POST, retorna URL Portal |
| `/conta/cancelar/`, `/conta/retomar/` | POST, cancelamento agendado/desfazer |
| `/b/<slug>/` | GET, cadeiras públicas |
| `/b/<slug>/equipe/` | GET, descritor de login operacional |
| `/b/<slug>/api/...` | APIs operacionais anteriores, com prefixo de tenant obrigatório |
| `/plataforma/` | GET autenticado GTS, empresas/assinaturas/erros, paginação `?offset=50` |
| `/plataforma/empresas/<uuid>/situacao/` | POST `{ativo: false, motivo: "..."}` bloqueia; `true` desbloqueia |
| `/plataforma/empresas/<uuid>/sincronizar/` | POST, sincronização controlada |
| `/plataforma/eventos/<id>/reprocessar/` | POST, agenda novamente evento com erro |

As APIs antigas de cadastrar/editar equipe e consultar/remover fila continuam com seus contratos e prefixo `/b/<slug>`. Login de dono por senha antigo responde 403 direcionando ao Google. `Cookie token_acesso_fila` tem path por tenant e sua busca também verifica o tenant no servidor. Todas as mutações de navegador usam CSRF. Conta retorna `CADASTRO_BARBEARIA`, `SEM_ASSINATURA`, `CHECKOUT_PENDENTE`, `PAGAMENTO_PENDENTE`, `PROVISIONAMENTO_PENDENTE`, `CONFIGURAR_EQUIPE`, `ATIVA`, `TOLERANCIA`, `CANCELAMENTO_AGENDADO`, `ENCERRADA`, `SUSPENSA` ou `BLOQUEIO_ADMINISTRATIVO`; fornece erro recuperável e ação de regularização quando aplicável.

O admin privado mostra cobrança apenas para consulta; não oferece “marcar pago” nem CRUD de participações pessoais. O painel GTS tem ações controladas e registro de autor/motivo. A URL privada continua vindo de `DJANGO_ADMIN_SECRET_PATH`.

## Recuperação

- Timeout na criação: repetir o mesmo POST reaproveita reserva/chave. Nunca limpar reservas ou trocar chaves para “tentar novamente”.
- Customer sem resposta local por mais de 23h: a integração para de criá-lo para não ultrapassar a janela de idempotência. Localize o Customer no Dashboard usando metadata de UUID e execute `recuperar_integracao --slug SLUG --customer-id cus_...`; valida ambiente e metadata antes de reassociar.
- Checkout sem ID local: reconciliação procura sessões por metadata. Após expiração fixa mais dez minutos, somente uma listagem remota completa sem sessão/assinatura permite encerrar a reserva sem apagar histórico. Falha ou limite da listagem mantém a reserva para revisão; use `recuperar_integracao --slug SLUG --checkout-id cs_...` para registrar o objeto remoto correto. Não crie nova tentativa enquanto houver ambiguidade.
- Após recuperar IDs, execute `reconciliar_saas --barbearia UUID`. A recuperação de IDs nunca marca pagamento; sincronização valida o pagamento real.
- Falha de provisionamento depois do pagamento: reconciliação reaplica provisionamento, sem Checkout novo.
- Evento com erro: veja diagnóstico em `/plataforma/` ou admin; corrija configuração/integração e use ação de reprocessamento. Erros guardam códigos/classes, sem mensagens remotas contendo PII/segredos.
- Lease abandonada após queda: aguarde até dez minutos para nova tentativa; um processo antigo não pode persistir resultados sem possuir a lease atual.
- Bloqueio GTS: regularizar pagamento não desbloqueia; ação administrativa com motivo é obrigatória.

## Validação e limites da entrega

Resultado final local (04/10/2026): **119 testes aprovados** em execução consolidada; `manage.py check`, verificação de migrations e `pip check` sem pendências. Migração aplicada ao banco SaaS após validar uma cópia: três usuários, dois barbeiros e nenhuma participação preservados, uma empresa criada, nenhuma assinatura concedida. `PRAGMA integrity_check` retornou `ok` e `foreign_key_check` não encontrou violações; os hashes das fotos conferem com o backup. Rotas públicas e proteções autenticadas foram verificadas no banco migrado. Os arquivos CSS/JS/imagens operacionais não foram modificados nesta etapa.

Baseline: 81 testes originais aprovados antes de alterações. Os testes operacionais foram adaptados apenas às pré-condições de tenant pago, papel de dono e rotas novas; regras de chamada/faltas/exclusão continuam cobertas. A suíte SaaS inclui isolamento, constraints, OAuth/UID local, onboarding, estados de acesso, CSRF, concorrência real de Checkout com conexões independentes, timeout/chaves, pagamento e provisionamento, callbacks assinados, duplicação/ordem de eventos, recontratação, Portal e migração de banco com participação existente.

```powershell
.\.venv\Scripts\python.exe manage.py test --noinput
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Mocks de transporte Stripe e fluxos internos allauth não equivalem a homologação externa. Ainda requer configurar credenciais reais de **teste**, OAuth Client e callback, Price/Portal, webhook e executar Checkout completo, Stripe CLI, Test Clocks e Google no navegador. Produção exige configuração própria e validação posterior; nenhuma implantação foi realizada. O layout final e auditoria ampla ficam para as próximas etapas solicitadas.
