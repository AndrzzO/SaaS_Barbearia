# Telas comerciais GTS

As páginas estão em / (vendas), /criar-conta/, /entrar/, /planos/ e /minha-conta/ (configuração e situação da conta).

A marca usada é o arquivo original fornecido pelo usuário, copiado sem alteração para saas/static/saas/logo-oficial.png. Os templates usam o componente mark.html. A arte do quadro e das cadeiras foi refeita com a marca integrada na própria imagem hero-barbearia-v2.png, sem sobreposição HTML ou fundo esfumado sobre o quadro.

As três referências originais permanecem em saas/static/saas/referencias/. Os recortes SVG/CSS reutilizam o celular, castelos e árvores originais. Textos, navegação, formulários e botões são HTML funcional. O layout responde à largura da tela mantendo a paleta azul/dourada e a composição das referências.

## Fluxos
- Começar agora leva ao cadastro com Google.
- Login/cadastro delegam a autenticação ao allauth; após entrar, o proprietário vai para /minha-conta/.
- O proprietário configura a barbearia e prossegue ao plano.
- Assinar encaminha visitantes ao cadastro, proprietários sem barbearia à configuração e proprietários elegíveis ao checkout existente.
- Assinaturas existentes apresentam gerenciamento, evitando oferecer uma nova contratação.
- Ativação depende de pagamento confirmado, conforme as regras existentes do backend.

## Pendências externas
Configurar Google OAuth e Stripe conforme SAAS.md. O fluxo externo real depende dessas credenciais e dos webhooks. As telas não simulam pagamento nem login bem-sucedido.
Substituir as mensagens informativas de contato, redes sociais, termos e privacidade pelos canais e documentos oficiais antes da abertura comercial.

## Verificação
Testes de páginas em saas/tests/test_paginas.py cobrem marca oficial, encaminhamento, login obrigatório, integração ausente, POST/CSRF e gerenciamento de assinatura existente. Conferência no navegador em desktop e celular de 390px.

## Revisão da arte das cadeiras
Ferramenta: image_gen integrada (edição com referência), sem CLI. Arquivo: saas/static/saas/hero-barbearia-v2.png.
Prompt: extrair a cena da referência fornecida, remover os elementos da interface, preservar cadeiras pretas e douradas, luzes e paleta azul; integrar a logo oficial ao quadro com perspectiva e iluminação natural, texto GUILD TECH SUPPORT, sem remendo preto ou marca colada.
