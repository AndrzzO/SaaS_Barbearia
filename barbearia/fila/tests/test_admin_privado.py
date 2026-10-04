from django.test import TestCase
from django.conf import settings
from django.contrib.auth import get_user_model

class AdminPrivadoTests(TestCase):
    def setUp(self):
        self.rota = '/' + settings.ADMIN_SECRET_PATH + '/'

    def test_visitante_precisa_login(self):
        resposta=self.client.get(self.rota)
        self.assertEqual(resposta.status_code,302)
        self.assertIn(self.rota+'login/',resposta.url)
        self.assertEqual(self.client.get(self.rota+'login/').status_code,200)

    def test_staff_nao_tem_acesso(self):
        usuario=get_user_model().objects.create_user('staff_teste',password='SenhaSegura123!',is_staff=True)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.rota).status_code,302)
        self.client.logout()
        resposta=self.client.post(self.rota+'login/',{'username':'staff_teste','password':'SenhaSegura123!','next':self.rota})
        self.assertEqual(resposta.status_code,200)
        self.assertNotIn('_auth_user_id',self.client.session)

    def test_proprietario_tem_acesso(self):
        usuario=get_user_model().objects.create_superuser('dono_admin',password='SenhaSegura123!')
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.rota).status_code,200)

    def test_endereco_nao_aparece_em_home_ou_404(self):
        self.assertNotContains(self.client.get('/'),settings.ADMIN_SECRET_PATH)
        resposta=self.client.get('/admin/')
        self.assertEqual(resposta.status_code,404)
        self.assertNotIn(settings.ADMIN_SECRET_PATH,resposta.content.decode())
