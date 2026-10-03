"""Regressões dos contratos usados pela interface e transições atômicas."""
import json
from datetime import date, timedelta
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone
from fila.models import Barbeiro, ParticipacaoFila, StatusParticipacao
from fila.services import (entrar_na_fila, iniciar_atendimento, chamar_especifico,
    chamar_proximo_manual, alternar_status_cadeira, ConflitoEstadoError)

class InterfaceTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_user('teste', password='Senha-segura-387!')
        self.barbeiro = Barbeiro.objects.create(usuario=self.usuario, nome_exibicao='Rafael', online=True)

    def entrar(self, numero=1):
        return entrar_na_fila(self.barbeiro.pk, 'Cliente', 'Teste', f'1198765432{numero}', date(1990, 1, 1))

    def test_paginas_publicas_e_csrf(self):
        for rota in ['/', '/minha-fila/', '/acesso/barbeiro/', '/acesso/gestao/', f'/cadastro/{self.barbeiro.pk}/']:
            resposta = self.client.get(rota)
            self.assertEqual(resposta.status_code, 200)
            self.assertContains(resposta, 'manifest.webmanifest')
            self.assertIn('csrftoken', resposta.cookies)
            self.assertIn('no-store', resposta['Cache-Control'])

    def test_protecao_paginas_privadas(self):
        for rota in ['/barbeiro/', '/barbeiro/perfil/', '/gestao/', '/gestao/equipe/', '/gestao/clientes/']:
            self.assertEqual(self.client.get(rota).status_code, 302)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get('/barbeiro/').status_code, 200)
        self.assertEqual(self.client.get('/gestao/').status_code, 302)

    def test_manifesto_e_worker_sem_cache_privado(self):
        manifesto=self.client.get('/manifest.webmanifest').json()
        self.assertEqual(manifesto['display'], 'standalone')
        self.assertEqual(len(manifesto['icons']), 2)
        worker=self.client.get('/sw.js')
        self.assertEqual(worker['Service-Worker-Allowed'], '/')
        self.assertNotContains(worker, "cache.put")
        self.assertContains(worker, "PUBLICOS.includes(url.pathname)")

    def test_csrf_obrigatorio_nas_alteracoes(self):
        cliente=Client(enforce_csrf_checks=True)
        resposta=cliente.post('/api/barbeiro/auth/login/', json.dumps({'username':'teste','senha':'Senha-segura-387!'}),content_type='application/json')
        self.assertEqual(resposta.status_code,403)

    def test_api_rejeita_json_nao_objeto_e_tipos_invalidos(self):
        for dados in [[], None, {'username': 42}, {'senha': False}]:
            resposta=self.client.post('/api/barbeiro/auth/login/', json.dumps(dados), content_type='application/json')
            self.assertEqual(resposta.status_code,400)
        self.client.force_login(self.usuario)
        resposta=self.client.post('/api/barbeiro/cadeira/status/', json.dumps({'online':'false'}), content_type='application/json')
        self.assertEqual(resposta.status_code,400)
        self.barbeiro.refresh_from_db()
        self.assertTrue(self.barbeiro.online)

    def test_posicao_inclui_cliente_chamado(self):
        self.entrar(1)
        p,token,_=self.entrar(2)
        self.client.cookies[settings.FILA_COOKIE_NOME]=token
        dados=self.client.get('/api/cliente/acompanhamento/').json()
        self.assertEqual(dados['clientes_na_frente'],1)
        self.assertEqual(dados['entrou_na_fila_em'],p.entrou_na_fila_em.isoformat())
        self.assertNotIn('telefone',dados)
        self.assertNotIn('data_nascimento',dados)

    def test_token_de_outro_cliente_nao_e_identificador_publico(self):
        p,token,_=self.entrar()
        self.client.cookies[settings.FILA_COOKIE_NOME]=str(p.identificador_publico)
        self.assertEqual(self.client.get('/api/cliente/acompanhamento/').json()['status'],'ENCERRADO')
        self.client.cookies[settings.FILA_COOKIE_NOME]=token
        self.assertNotEqual(self.client.get('/api/cliente/acompanhamento/').json()['status'],'ENCERRADO')

    def test_expiracao_persistida_no_inicio_rejeitado(self):
        p,_,_=self.entrar(1)
        segundo,_,_=self.entrar(2)
        with patch('fila.services.timezone.now', return_value=p.chamada_expira_em):
            with self.assertRaises(ConflitoEstadoError):
                iniciar_atendimento(self.barbeiro,p.pk,str(p.identificador_chamada))
        p.refresh_from_db();segundo.refresh_from_db()
        self.assertTrue(p.aguardando_ultrapassagem)
        self.assertEqual(p.ultrapassagens_por_falta,0)
        self.assertEqual(segundo.status,StatusParticipacao.CHAMADO)

    def test_chamada_manual_apos_expiracao_nao_duplica_chamados(self):
        p,_,_=self.entrar(1)
        segundo,_,_=self.entrar(2)
        terceiro,_,_=self.entrar(3)
        with patch('fila.services.timezone.now', return_value=p.chamada_expira_em):
            chamado=chamar_especifico(self.barbeiro,terceiro.pk)
        self.assertEqual(chamado.pk,terceiro.pk)
        self.assertEqual(ParticipacaoFila.objects.filter(status='CHAMADO').count(),1)
        p.refresh_from_db()
        self.assertTrue(p.aguardando_ultrapassagem)

    def test_ciclo_anterior_nao_inicia_chamada_nova(self):
        p,_,_=self.entrar(1)
        segundo,_,_=self.entrar(2)
        ciclo=str(p.identificador_chamada)
        chamar_especifico(self.barbeiro,segundo.pk)
        chamar_especifico(self.barbeiro,p.pk)
        with self.assertRaises(ConflitoEstadoError):
            iniciar_atendimento(self.barbeiro,p.pk,ciclo)
        p.refresh_from_db()
        self.assertEqual(p.status,StatusParticipacao.CHAMADO)

    def test_barbeiro_obsoleto_nao_chama_cadeira_offline(self):
        antigo=Barbeiro.objects.get(pk=self.barbeiro.pk)
        alternar_status_cadeira(self.barbeiro,False)
        with self.assertRaises(ConflitoEstadoError):
            chamar_proximo_manual(antigo)

    def test_get_acompanhamento_nao_processa_expiracao(self):
        p,token,_=self.entrar()
        self.client.cookies[settings.FILA_COOKIE_NOME]=token
        with patch('fila.views.timezone.now', return_value=p.chamada_expira_em+timedelta(seconds=2)):
            dados=self.client.get('/api/cliente/acompanhamento/').json()
        self.assertEqual(dados['tempo_restante_segundos'],0)
        p.refresh_from_db()
        self.assertEqual(p.status,StatusParticipacao.CHAMADO)

    def test_edicao_generica_admin_nao_disponivel(self):
        self.assertEqual(self.client.get('/admin/').status_code,404)

    def test_chamar_proximo_persiste_expiracao_sem_outro_elegivel(self):
        p,_,_=self.entrar()
        with patch('fila.services.timezone.now', return_value=p.chamada_expira_em):
            with self.assertRaises(ConflitoEstadoError):
                chamar_proximo_manual(self.barbeiro)
        p.refresh_from_db()
        self.assertEqual(p.status, StatusParticipacao.AGUARDANDO)
        self.assertTrue(p.aguardando_ultrapassagem)
        self.assertEqual(p.ultrapassagens_por_falta, 0)

    def test_criar_proprietario_interativo(self):
        from django.core.management import call_command
        from io import StringIO
        with patch('builtins.input', return_value='dono_teste'), patch('fila.management.commands.criar_proprietario.getpass.getpass', return_value='Senh@-exclusiva-73921'):
            call_command('criar_proprietario', stdout=StringIO())
        dono=get_user_model().objects.get(username='dono_teste')
        self.assertTrue(dono.is_superuser)
        self.assertTrue(dono.check_password('Senh@-exclusiva-73921'))

    def test_criar_proprietario_rejeita_senha_fraca_sem_criar_conta(self):
        from django.core.management import call_command, CommandError
        with self.assertRaises(CommandError):
            call_command('criar_proprietario', username='dono_fraco', senha='123')
        self.assertFalse(get_user_model().objects.filter(username='dono_fraco').exists())
