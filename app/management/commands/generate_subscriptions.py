"""Lança a cobrança do mês das assinaturas que já passaram do dia de cobrança.

A assinatura é um cadastro que não termina: enquanto existir, todo mês ela deve
virar uma transação. Isso é trabalho que acontece na passagem do dia, não no
clique de ninguém — e é por isso que ele mora num comando, e não no save() do
formulário.

O que o comando faz é idempotente: cada assinatura guarda a última competência
lançada, e é dela que sai a próxima. Rodar duas vezes no mesmo dia não duplica
nada, e ficar uma semana sem rodar não perde mês nenhum — a execução seguinte
lança o que faltou, cada cobrança na fatura em que ela teria caído.

Não há agendador neste projeto, e um container só para isto seria caro demais
para uma tarefa de um instante por dia. Quem chama é o cron da máquina, pelo
alvo `generate-subscriptions` do Makefile. As telas do sistema também chamam a
mesma geração ao serem abertas, para que a ausência do cron atrase a cobrança
em vez de sumir com ela.
"""

from django.core.management.base import BaseCommand

from app.models import Subscription


class Command(BaseCommand):
    help = 'Lança as cobranças mensais vencidas das assinaturas cadastradas.'

    def handle(self, *args, **options):
        created = Subscription.generate_due()
        self.stdout.write(f'{created} cobrança(s) de assinatura lançada(s).')
