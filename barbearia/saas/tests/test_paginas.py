from django.test import TestCase, Client, override_settings
from django.urls import reverse
from .test_saas import Fixture


class PaginasTests(Fixture, TestCase):
    def test_paginas_publicas_com_logo_oficial(self):
        for url in ('/', '/entrar/', '/criar-conta/', '/planos/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, 'saas/logo-oficial.png')
                self.assertContains(response, 'saas/site.css')

    def test_assinatura_anonima_encaminha_cadastro(self):
        self.assertRedirects(self.client.post('/assinar/'), '/criar-conta/')
        response = self.client.post('/assinar/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.json(), {'redirecionar': '/criar-conta/'})

    def test_painel_exige_login(self):
        self.assertRedirects(self.client.get('/minha-conta/'), '/entrar/')

    @override_settings(SOCIALACCOUNT_PROVIDERS={'google': {'APP': {}}})
    def test_google_sem_configuracao_mantem_pagina_e_informa(self):
        response = self.client.post('/acessar-google/', {'cadastro': '1'})
        self.assertContains(response, 'O acesso com Google está sendo preparado', status_code=503)
        self.assertContains(response, 'Crie sua conta', status_code=503)

    def test_acoes_exigem_post_e_csrf(self):
        protected = Client(enforce_csrf_checks=True)
        for url in ('/acessar-google/', '/assinar/'):
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(protected.post(url).status_code, 403)

    def test_assinante_ve_gerenciamento(self):
        self.preparar()
        self.client.force_login(self.u)
        response = self.client.get('/planos/')
        self.assertContains(response, 'Gerenciar assinatura')
        self.assertNotContains(response, 'data-checkout-form')
        self.assertEqual(self.client.get('/minha-conta/').status_code, 200)
