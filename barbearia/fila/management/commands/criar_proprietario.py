"""Criação local do proprietário com senha oculta no modo interativo."""
import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

class Command(BaseCommand):
    help = 'Cria ou atualiza o proprietário; sem parâmetros, solicita usuário e senha oculta.'

    def add_arguments(self, parser):
        parser.add_argument('--username', default=None)
        parser.add_argument('--senha', default=None, help='Prefira o modo interativo para não expor a senha no histórico do terminal.')
        parser.add_argument('--email', default='')

    def handle(self, *args, **options):
        username = (options['username'] or input('Usuário do proprietário: ')).strip()
        senha = options['senha']
        if senha is None:
            senha = getpass.getpass('Senha: ')
            if senha != getpass.getpass('Confirme a senha: '):
                raise CommandError('As senhas não coincidem.')
        if not username or not senha:
            raise CommandError('Usuário e senha são obrigatórios.')
        User = get_user_model()
        candidato = User(username=username, email=options['email'])
        try:
            candidato.full_clean(exclude=['password'], validate_unique=False)
            validate_password(senha, candidato)
        except ValidationError as exc:
            raise CommandError(' '.join(exc.messages)) from exc
        with transaction.atomic():
            usuario, criado = User.objects.get_or_create(username=username)
            usuario.set_password(senha)
            usuario.is_staff = usuario.is_superuser = usuario.is_active = True
            if options['email']:
                usuario.email = options['email']
            usuario.save()
        self.stdout.write(self.style.SUCCESS('Proprietário criado.' if criado else 'Proprietário atualizado.'))
