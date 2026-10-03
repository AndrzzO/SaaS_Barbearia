"""
Comando de gerenciamento para supervisão contínua do processamento de filas e prazos de chamada.
Guild Tech Support.
Executa como processo separado supervisionado no mesmo servidor (Passo 13).
"""

import signal
import sys
import time
from django.core.management.base import BaseCommand
from fila.services import processar_filas_servico


class Command(BaseCommand):
    help = "Supervisiona e processa chamadas expiradas e novas chamadas elegíveis na fila da barbearia."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executando = True

    def encerramento_gracioso(self, signum, frame):
        self.stdout.write(self.style.WARNING(f"\nSinal {signum} recebido. Encerrando o processador de filas graciosamente..."))
        self.executando = False

    def add_arguments(self, parser):
        parser.add_argument(
            '--intervalo',
            type=float,
            default=1.0,
            help="Intervalo em segundos entre ciclos de verificação (padrão: 1.0s)."
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help="Executa um único ciclo de processamento e encerra (ideal para testes/verificação)."
        )

    def handle(self, *args, **options):
        intervalo = options['intervalo']
        apenas_uma_vez = options['once']

        signal.signal(signal.SIGINT, self.encerramento_gracioso)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self.encerramento_gracioso)

        self.stdout.write(self.style.SUCCESS(
            f"Processador de filas iniciado. Intervalo: {intervalo}s. Pressione Ctrl+C para encerrar."
        ))

        while self.executando:
            try:
                operacoes = processar_filas_servico()
                if operacoes > 0:
                    self.stdout.write(f"Ciclo executado: {operacoes} operação(ões) realizada(s).")
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Erro ao processar ciclo de filas: {exc}"))

            if apenas_uma_vez:
                break

            # A pausa (sleep) é mantida FORA de transações
            tempo_dormir = 0.0
            passo = 0.2
            while self.executando and tempo_dormir < intervalo:
                time.sleep(min(passo, intervalo - tempo_dormir))
                tempo_dormir += passo

        self.stdout.write(self.style.SUCCESS("Processador de filas finalizado com sucesso."))
