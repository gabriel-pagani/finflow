"""Cálculos de calendário que o domínio financeiro usa, sem depender de modelo.

Ficam fora do models.py porque não são comportamento de nenhum registro: são
aritmética de data que tanto o cartão quanto a assinatura precisam, e que os
testes conferem isoladamente.
"""

from datetime import timedelta

from django.utils import timezone


# Sábado e domingo no weekday() do Python, que conta a partir da segunda.
WEEKEND = (5, 6)


def add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])

    return dt.replace(year=year, month=month, day=day)


def current_month():
    """O número do mês corrente, default do mês de referência da assinatura.

    É função, e não o valor calculado na importação: um default avaliado uma vez
    prenderia todo cadastro ao mês em que o processo subiu.
    """
    return timezone.localdate().month


def next_business_day(day):
    """Empurra sábado e domingo para a segunda-feira seguinte.

    Feriado não entra na conta: o sistema não mantém calendário deles, e supor
    um significaria escolher entre nacional, estadual e municipal sem ter como
    saber qual vale para o cartão.
    """
    while day.weekday() in WEEKEND:
        day += timedelta(days=1)
    return day
