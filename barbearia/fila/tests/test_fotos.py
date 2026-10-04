from io import BytesIO
from tempfile import TemporaryDirectory
from pathlib import Path
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from PIL import Image
from fila.models import Barbeiro, ParticipacaoFila
from fila.admin import BarbeiroAdmin, site_proprietario

class FotosBarbeiroTests(TestCase):
    def setUp(self):
        self.pasta=TemporaryDirectory()
        self.config=override_settings(MEDIA_ROOT=self.pasta.name)
        self.config.enable()
        self.addCleanup(self.pasta.cleanup)
        self.addCleanup(self.config.disable)
        self.dono=get_user_model().objects.create_superuser('dono_foto',password='Senha-exclusiva-928!')
        self.client.force_login(self.dono)

    def foto(self, cor='red'):
        buffer=BytesIO()
        Image.new('RGB',(80,60),cor).save(buffer,format='PNG')
        return SimpleUploadedFile('minha-foto.png',buffer.getvalue(),content_type='image/png')

    def criar(self, foto=None):
        dados={'username':'barbeiro_foto','senha':'Senha-exclusiva-567!','nome_exibicao':'Meu Barbeiro'}
        if foto is not None: dados['foto']=foto
        return self.client.post('/api/proprietario/barbeiros/cadastrar/',dados)

    def test_upload_multipart_e_foto_publica(self):
        resposta=self.criar(self.foto())
        self.assertEqual(resposta.status_code,201,resposta.content)
        b=Barbeiro.objects.get(pk=resposta.json()['barbeiro_id'])
        self.assertTrue(b.foto.name.endswith('.jpg'))
        self.assertNotIn('minha-foto',b.foto.name)
        with Image.open(b.foto.path) as foto:
            self.assertEqual(foto.format,'JPEG')
            self.assertFalse(foto.getexif())
        dados=self.client.get('/api/publico/cadeiras/').json()['cadeiras'][0]
        self.assertEqual(dados['foto_url'],b.foto.url)
        self.assertEqual(self.client.get('/api/proprietario/barbeiros/').json()['barbeiros'][0]['foto_url'],b.foto.url)

    def test_upload_opcional(self):
        resposta=self.criar()
        self.assertEqual(resposta.status_code,201)
        self.assertIsNone(resposta.json()['foto_url'])

    def test_troca_e_remocao_excluem_arquivo_anterior(self):
        b=Barbeiro.objects.get(pk=self.criar(self.foto()).json()['barbeiro_id'])
        anterior=b.foto.path
        url=f'/api/proprietario/barbeiros/{b.pk}/editar/'
        with self.captureOnCommitCallbacks(execute=True):
            resposta=self.client.post(url,{'nome_exibicao':'Nome novo','foto':self.foto('blue')})
        self.assertEqual(resposta.status_code,200,resposta.content)
        self.assertFalse(Path(anterior).exists())
        b.refresh_from_db();atual=b.foto.path
        self.assertTrue(Path(atual).exists())
        with self.captureOnCommitCallbacks(execute=True):
            resposta=self.client.post(url,{'nome_exibicao':'Nome novo','remover_foto':'on'})
        self.assertEqual(resposta.status_code,200)
        self.assertFalse(Path(atual).exists())
        b.refresh_from_db();self.assertFalse(b.foto)

    def test_imagem_falsa_rejeitada_sem_criar_usuario(self):
        resposta=self.criar(SimpleUploadedFile('foto.png',b'<script>alert(1)</script>',content_type='image/png'))
        self.assertEqual(resposta.status_code,400)
        self.assertFalse(get_user_model().objects.filter(username='barbeiro_foto').exists())
        self.assertEqual(list(Path(self.pasta.name).rglob('*.jpg')),[])

    def test_limite_de_cinco_mb(self):
        resposta=self.criar(SimpleUploadedFile('foto.jpg',b'x'*(5*1024*1024+1),content_type='image/jpeg'))
        self.assertEqual(resposta.status_code,400)

    def test_sem_permissao_nao_envia_foto(self):
        self.client.logout()
        self.assertEqual(self.criar(self.foto()).status_code,401)
        u=get_user_model().objects.create_user('comum')
        self.client.force_login(u)
        self.assertEqual(self.criar(self.foto()).status_code,403)
        self.assertEqual(list(Path(self.pasta.name).rglob('*.jpg')),[])

    def test_editar_nome_sem_foto_preserva_foto(self):
        b=Barbeiro.objects.get(pk=self.criar(self.foto()).json()['barbeiro_id'])
        anterior=b.foto.name
        resposta=self.client.post(f'/api/proprietario/barbeiros/{b.pk}/editar/',{'nome_exibicao':'Novo nome'})
        self.assertEqual(resposta.status_code,200)
        b.refresh_from_db();self.assertEqual(b.foto.name,anterior)

    def test_foto_exclusiva_do_barbeiro_inclusive_admin(self):
        self.assertNotIn('foto',[campo.name for campo in ParticipacaoFila._meta.fields])
        from django.test import RequestFactory
        request=RequestFactory().get('/')
        request.user=self.dono
        form=BarbeiroAdmin(Barbeiro,site_proprietario).get_form(request)
        self.assertIn('foto',form.base_fields)
