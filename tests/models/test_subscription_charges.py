"""
O lançamento automático das cobranças.

A mesma competência nunca sai duas vezes: quem garante é a unicidade de
assinatura e competência. Cada período tem a sua própria fase, então reativar
retoma o ciclo da data nova sem preencher o intervalo encerrado.
"""
from datetime import date
from decimal import Decimal

from app.models import Method, Nature, Recurrence, Subscription, Transaction, Type


def test_lanca_a_cobranca_do_mes(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 9, 10))
    cobrancas = assinatura.generate_charges(date(2026, 9, 10))
    assert len(cobrancas) == 1
    assert cobrancas[0].reference == date(2026, 9, 1)
    assert cobrancas[0].occurred_at == date(2026, 9, 10)


def test_a_cobranca_herda_os_dados_da_assinatura(subscribe, credit_rule, category):
    assinatura = subscribe(category=category, value=Decimal('55.90'))
    cobranca = assinatura.generate_charges(date(2026, 9, 10))[0]
    assert cobranca.value == Decimal('55.90')
    assert cobranca.card_id == assinatura.card_id
    assert cobranca.category_id == category.pk
    assert cobranca.type == Type.OUT
    assert cobranca.method == Method.CREDIT
    assert cobranca.nature == Nature.REGULAR


def test_a_cobranca_cai_na_fatura_do_cartao(subscribe, credit_rule):
    """Cartão que fecha dia 5 e vence dia 12: cobrança do dia 10 cai na fatura seguinte."""
    assinatura = subscribe(started_at=date(2026, 9, 10))
    cobranca = assinatura.generate_charges(date(2026, 9, 10))[0]
    assert cobranca.effective_at == date(2026, 10, 12)


def test_rodar_de_novo_nao_duplica(subscribe, credit_rule):
    assinatura = subscribe()
    assinatura.generate_charges(date(2026, 9, 10))
    assert assinatura.generate_charges(date(2026, 9, 10)) == []
    assert assinatura.transactions.count() == 1


def test_meses_em_atraso_saem_de_uma_vez(subscribe, credit_rule):
    """Sem acesso depois de setembro: em dezembro saem as três que faltaram."""
    assinatura = subscribe(started_at=date(2026, 9, 10))
    assinatura.generate_charges(date(2026, 9, 10))
    cobrancas = assinatura.generate_charges(date(2026, 12, 10))
    assert [c.reference for c in cobrancas] == [
        date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1),
    ]


def test_periodo_antigo_sai_inteiro_no_primeiro_acesso(subscribe, credit_rule):
    """Assinatura cadastrada com início em janeiro gera as nove competências devidas."""
    assinatura = subscribe(started_at=date(2026, 1, 2))
    cobrancas = assinatura.generate_charges(date(2026, 9, 9))
    assert len(cobrancas) == 9
    assert cobrancas[0].reference == date(2026, 1, 1)
    assert cobrancas[-1].reference == date(2026, 9, 1)


def test_nao_retroage_antes_do_inicio_do_periodo(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 12, 10))
    assert assinatura.generate_charges(date(2026, 12, 10))[0].reference == date(2026, 12, 1)
    assert assinatura.transactions.count() == 1


def test_anual_passa_o_ano_sem_lancar_nada(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 9, 10), recurrence=Recurrence.ANNUAL)
    assinatura.generate_charges(date(2026, 9, 10))
    assert assinatura.generate_charges(date(2027, 8, 31)) == []
    assert len(assinatura.generate_charges(date(2027, 9, 10))) == 1


def test_encerrada_para_de_lancar(subscribe, credit_rule):
    assinatura = subscribe(started_at=date(2026, 9, 10), cancelled_at=date(2026, 9, 20))
    assinatura.generate_charges(date(2026, 9, 10))
    assert assinatura.generate_charges(date(2026, 10, 10)) == []


def test_encerrada_antes_do_dia_da_cobranca_nao_lanca_o_mes(subscribe, credit_rule):
    """Cobrando dia 10 e encerrada em 05/09: agosto sai, setembro não."""
    assinatura = subscribe(started_at=date(2026, 8, 10), cancelled_at=date(2026, 9, 5))
    cobrancas = assinatura.generate_charges(date(2026, 9, 30))
    assert [c.reference for c in cobrancas] == [date(2026, 8, 1)]


def test_reativar_retoma_da_data_nova_e_pula_o_intervalo_encerrado(subscribe, make_period, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 12), cancelled_at=date(2026, 3, 13))
    assinatura.generate_charges(date(2026, 9, 30))
    make_period(assinatura, started_at=date(2026, 7, 12))
    cobrancas = assinatura.generate_charges(date(2026, 9, 30))
    assert [c.reference for c in cobrancas] == [
        date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1),
    ]


def test_reativar_em_outro_dia_muda_o_dia_das_cobrancas(subscribe, make_period, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 12), cancelled_at=date(2026, 3, 13))
    assinatura.generate_charges(date(2026, 9, 30))
    make_period(assinatura, started_at=date(2026, 7, 25))
    cobrancas = assinatura.generate_charges(date(2026, 9, 30))
    assert [c.occurred_at for c in cobrancas] == [
        date(2026, 7, 25), date(2026, 8, 25), date(2026, 9, 25),
    ]


def test_varios_ciclos_de_encerramento_e_reativacao(subscribe, make_period, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 2, 15))
    make_period(assinatura, started_at=date(2026, 5, 10), cancelled_at=date(2026, 6, 15))
    make_period(assinatura, started_at=date(2026, 9, 10))
    cobrancas = assinatura.generate_charges(date(2026, 9, 30))
    assert [c.reference for c in cobrancas] == [
        date(2026, 1, 1), date(2026, 2, 1),
        date(2026, 5, 1), date(2026, 6, 1),
        date(2026, 9, 1),
    ]


def test_assinatura_sem_periodo_nao_lanca_nada(make_subscription, credit_rule):
    assinatura = make_subscription()
    assinatura.save()
    assert assinatura.generate_charges(date(2026, 9, 10)) == []


def test_generate_due_conta_o_que_lancou(subscribe, credit_rule, user):
    subscribe(description='Netflix')
    subscribe(description='Spotify', value=Decimal('21.90'))
    assert Subscription.generate_due(user, today=date(2026, 9, 10)) == 2
    assert Subscription.generate_due(user, today=date(2026, 9, 10)) == 0


def test_generate_due_alcanca_periodo_antigo_acrescentado_depois(subscribe, make_period, credit_rule, user):
    """Registrar hoje um período de junho a agosto não pode ficar de fora por já haver cobrança de setembro."""
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 2, 15))
    make_period(assinatura, started_at=date(2026, 9, 10))
    Subscription.generate_due(user, today=date(2026, 9, 30))
    antes = assinatura.transactions.count()

    make_period(assinatura, started_at=date(2026, 5, 10), cancelled_at=date(2026, 6, 15))
    assert Subscription.generate_due(user, today=date(2026, 9, 30)) == 2
    assert assinatura.transactions.count() == antes + 2


def test_generate_due_alcanca_so_as_assinaturas_do_usuario(subscribe, credit_rule, other_user):
    subscribe()
    subscribe(user=other_user, description='Spotify')
    assert Subscription.generate_due(other_user, today=date(2026, 9, 10)) == 1
    assert Transaction.objects.count() == 1


def test_assinatura_com_periodo_aberto_esta_ativa(subscribe, make_period, credit_rule):
    assinatura = subscribe(started_at=date(2026, 1, 10), cancelled_at=date(2026, 2, 15))
    assert not assinatura.is_active
    make_period(assinatura, started_at=date(2026, 5, 10))
    assert Subscription.objects.get(pk=assinatura.pk).is_active
