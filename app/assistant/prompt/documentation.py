"""O que o assistente sabe antes da primeira mensagem.

A DOCUMENTATION era servida por uma rota, e o agente do n8n a pedia quando
lembrava. Aqui ela vai inline no system prompt, por dois motivos. O primeiro é
que a regra de negócio não muda entre uma pergunta e outra: pagá-la uma vez por
conversa, num prefixo que o cache do modelo cobra barato, sai mais em conta do
que uma chamada de ferramenta com round-trip inteiro. O segundo é o que importa:
uma ferramenta pode não ser chamada. Metade do prompt do n8n existia para mandar
o agente ler as regras antes de gravar dinheiro — e um "SEMPRE consulte" é um
pedido, não uma garantia. Inline, não há o que esquecer.
"""

DOCUMENTATION = {
    'visao_geral': (
        'FinFlow é um sistema de finanças pessoais. Tudo o que existe nele desemboca em '
        'Transação: é ela que compõe saldo, entrada, saída e previsão. Algumas transações '
        'são avulsas, criadas diretamente; outras são derivadas, geradas por um registro de '
        'origem (parcelamento, transferência ou investimento). Transação derivada nunca é '
        'criada nem editada diretamente — mexe-se na origem, e ela regera as filhas.'
    ),

    'como_registrar': {
        'ferramenta': 'registrar_lancamento',
        'discriminador': (
            'O campo "kind" escolhe o que será lançado, e é obrigatório. Os três valores '
            'correspondem exatamente às três opções do botão "Nova Transação" da interface.'
        ),
        'kinds': {
            'transaction': 'Lançamento único de entrada ou saída. Use para o caso comum: uma compra, um recebimento.',
            'installment': 'Compra no crédito dividida em parcelas mensais. Gera uma transação por parcela.',
            'transfer': 'Movimentação de valor entre duas contas do próprio usuário. Gera duas transações.',
        },
        'formatos': {
            'valor': (
                'Decimal em string ou número, com PONTO como separador decimal: "25.00", "1234.56". '
                'Vírgula é rejeitada ("1234,56" não é aceito). Não use separador de milhar. '
                'O valor deve ser sempre maior que zero: o que distingue entrada de saída é o campo '
                '"type", nunca o sinal do valor.'
            ),
            'data_hora': (
                'Aceita "2026-08-31T14:30", "2026-08-31 14:30", "2026-08-31T14:30:00-03:00" e o '
                'formato brasileiro "31/08/2026 14:30". Sem fuso explícito, entende-se '
                'America/Sao_Paulo. Se o campo for omitido, usa-se o instante atual — mesmo '
                'comportamento da tela, que já abre o campo preenchido com o agora.'
            ),
            'referencias': 'Conta, categoria, cartão e investimento são informados pelo "id" numérico devolvido por consultar_opcoes.',
        },
    },

    'regras_de_negocio': (
        'Cada conta declara quais combinações de tipo e método aceita, e uma combinação não '
        'cadastrada é recusada. Isso existe para o cadastro refletir o mundo: uma conta de '
        'investimento não recebe compra no crédito, um cartão pré-pago não tem débito. '
        'Antes de montar qualquer lançamento, consulte accounts[].allowed_combinations em '
        'consultar_opcoes: só o que estiver ali passa. Lançar fora disso retorna erro de '
        'validação, e nada é gravado nem proposto ao usuário.'
    ),

    'ciclo_do_cartao': {
        'resumo': (
            'ATENÇÃO — esta é a regra mais fácil de errar. Ao lançar no crédito, você informa a data '
            'da COMPRA, mas o que fica gravado na transação é a data de VENCIMENTO da fatura em que '
            'aquela compra caiu. Uma compra de hoje pode aparecer gravada semanas à frente. Isso é '
            'esperado: é o que faz a previsão de gastos bater com o que será efetivamente pago.'
        ),
        'como_a_fatura_e_escolhida': (
            'A compra entra na primeira fatura que ainda não fechou. Comprou ANTES do dia de '
            'fechamento, cai na fatura do mês corrente; comprou NO dia do fechamento ou depois, cai '
            'na do mês seguinte.'
        ),
        'fechamento_e_vencimento': (
            'Fechamento e vencimento são guardados como dia do mês, não como data, porque o ciclo se '
            'repete todo mês. O fechamento cai sempre no dia configurado, inclusive sábado e domingo: '
            'fechar é a operadora encerrar a fatura, e para isso não é preciso banco aberto. O '
            'vencimento, ao contrário, depende de banco aberto — se cair em fim de semana, anda para a '
            'segunda-feira seguinte. Feriado não entra na conta, porque o sistema não mantém calendário '
            'de feriados. Quando o dia de vencimento é menor ou igual ao de fechamento, o vencimento é '
            'do mês seguinte ao do fechamento. Num mês curto, um dia configurado além do fim do mês é '
            'limitado ao último dia dele: quem fecha dia 31 fecha dia 28 em fevereiro.'
        ),
        'exemplo': (
            'Cartão que fecha dia 20 e vence dia 27. Compra em 19/03: a fatura de março ainda não '
            'fechou, então a compra entra nela e é gravada em 27/03. Compra em 20/03 (o próprio dia do '
            'fechamento): já perdeu a fatura de março, entra na de abril e é gravada em 27/04. Compra '
            'em 25/03: idem, 27/04.'
        ),
        'campo_card': (
            'O cartão é obrigatório quando method="CREDIT" e ignorado nos demais métodos. É por ele '
            'que se descobre o ciclo. O cartão precisa ser do próprio usuário e da mesma conta '
            'informada no lançamento.'
        ),
        'efeito_na_analise': (
            'Como a transação do crédito é gravada na data de VENCIMENTO, uma análise por período '
            'agrupa a compra no mês em que ela será paga, não naquele em que foi feita. Perguntas '
            'sobre "quanto gastei no cartão em março" respondem-se com o vencimento de março.'
        ),
    },

    'parcelamento': {
        'o_que_e': 'Compra no crédito dividida em parcelas mensais. É sempre crédito, então o cartão é sempre obrigatório.',
        'valor': (
            'O campo "value" é o valor TOTAL da compra, não o da parcela. O sistema divide: cada '
            'parcela recebe o total dividido pelo número de parcelas, arredondado para baixo em dois '
            'decimais, e a ÚLTIMA parcela absorve a diferença que sobrou. Assim a soma das parcelas '
            'devolve exatamente o total, sem centavo perdido no arredondamento. '
            'Exemplo: 100.00 em 3x vira 33.33 + 33.33 + 33.34.'
        ),
        'minimo': 'São exigidas no mínimo 2 parcelas. Para 1 parcela, use kind="transaction".',
        'datas': (
            'A primeira parcela cai no vencimento da fatura em que a compra entrou, e cada parcela '
            'seguinte soma um mês sobre o ciclo, não sobre a data da parcela anterior — assim uma '
            'parcela não herda o empurrão de fim de semana que valia só para a outra.'
        ),
        'resultado': 'Gera uma transação por parcela, todas com type="OUT" e method="CREDIT", numeradas em "parcel".',
    },

    'transferencia': {
        'o_que_e': 'Movimentação de valor entre duas contas do próprio usuário. Não é receita nem despesa: o dinheiro só mudou de lugar.',
        'resultado': (
            'Gera DUAS transações, ambas com nature="INTERNAL": uma saída na conta de origem, com '
            'method="DEBIT", e uma entrada na conta de destino, com method="NOT_APPLICABLE". O destino '
            'não é débito porque classificá-lo assim o colocaria no mesmo balde de uma receita de '
            'verdade, inflando a entrada do período.'
        ),
        'exigencias': (
            'Origem e destino precisam ser contas diferentes. A origem precisa aceitar saída em Débito '
            'e o destino precisa aceitar entrada em Não Se Aplica — as duas regras, porque sem ambas a '
            'transferência gravaria metade e deixaria o saldo torto.'
        ),
    },

    'investimento': {
        'o_que_e': (
            'Uma posição investida (CDI, Tesouro Selic, LCI). Acumula aplicações, resgates e '
            'rendimentos, e o saldo é aplicado + rendido - resgatado.'
        ),
        'nao_editavel_pela_api': (
            'IMPORTANTE: investimento, aplicação, resgate e rendimento NÃO são criados por esta API '
            'nem pela interface — só pelo portal de administração. A consulta os expõe apenas para '
            'leitura e análise. Um agente não deve prometer ao usuário que registrou uma aplicação.'
        ),
        'efeito_em_transacao': (
            'Aplicação gera uma saída em Débito e resgate gera uma entrada em Não Se Aplica. '
            'Rendimento NÃO gera transação nenhuma: ele entra no saldo do investimento, mas não no '
            'saldo em conta, porque o dinheiro rendeu sem sair nem entrar em lugar nenhum.'
        ),
    },

    'naturezas': {
        'REGULAR': 'Movimento comum. É o padrão quando o campo é omitido, e o único que entra nas análises de entrada e saída.',
        'INTERNAL': 'Movimentação interna, aplicada automaticamente às duas pernas da transferência. Fica fora das análises para não contar o mesmo dinheiro dos dois lados.',
        'ADJUSTMENT': 'Ajuste de saldo, para corrigir divergência com o extrato real. Conta no saldo, mas fica fora das análises de entrada e saída.',
        'como_usar': 'Deixe em branco (ou "REGULAR") em praticamente todo lançamento. Use "ADJUSTMENT" apenas quando o objetivo for corrigir o saldo, não registrar um gasto ou receita real.',
    },

    'analise': {
        'ferramenta': 'consultar_financas',
        'como_funciona': (
            'Os filtros recortam um conjunto de transações; as seções pedidas em "include" descrevem '
            'esse mesmo conjunto de vários ângulos. Todo parâmetro que aceita lista recebe valores '
            'separados por vírgula. A resposta devolve em "filters" o recorte que realmente valeu, '
            'inclusive os padrões aplicados — confira ali antes de afirmar um número ao usuário.'
        ),
        'periodo': {
            'start / end': (
                'Recorte explícito, inclusivo nas duas pontas. Aceita "2026-03-15", "2026-03" (o mês '
                'inteiro: start vira o dia 1, end vira o último dia), "2026" (o ano inteiro) e '
                '"15/03/2026". Sem "end", vale hoje.'
            ),
            'months': (
                'Alternativa a "start": janela móvel de N meses cheios terminando no mês de "end". '
                'months=1 é o mês corrente, months=12 são os últimos doze. Ignorado se "start" vier.'
            ),
            'padrao': 'Sem nenhum dos três, analisam-se os últimos 12 meses.',
        },
        'filtros': {
            'account': 'Ids de conta, separados por vírgula. Ex.: account=1,3.',
            'category': 'Ids de categoria. Aceita o valor "null" para as transações sem categoria. Ex.: category=2,null.',
            'card': 'Ids de cartão. Aceita "null" para o que não passou em cartão.',
            'type': 'IN, OUT ou ambos. Sem o parâmetro, os dois entram.',
            'method': (
                'CREDIT, DEBIT, NOT_APPLICABLE. PADRÃO: DEBIT e NOT_APPLICABLE, o dinheiro que já saiu '
                'da conta. Para analisar o cartão passe method=CREDIT; para tudo, method=all.'
            ),
            'nature': (
                'REGULAR, INTERNAL, ADJUSTMENT. PADRÃO: REGULAR, senão transferência e ajuste inflariam '
                'entrada e saída dos dois lados. Para tudo, nature=all.'
            ),
            'origin': (
                'De onde a transação veio: standalone (avulsa), installment (parcela), transfer (perna '
                'de transferência) ou investment (aplicação/resgate). Ex.: origin=installment responde '
                '"quanto do meu gasto é parcelado".'
            ),
            'min_value / max_value': 'Faixa de valor de cada transação, não do total.',
            'search': 'Trecho contido na descrição, sem diferenciar maiúsculas.',
        },
        'group_by': (
            'Eixos pelos quais os totais são quebrados, separados por vírgula. Aceita: month, day, '
            'week, year, category, account, card, method, type, nature. PADRÃO: month,category. Cada '
            'eixo devolve linhas {key, label, income, outcome, net, count}, e as linhas de todos os '
            'eixos somam o mesmo total — são recortes do mesmo conjunto, não subconjuntos dele.'
        ),
        'include': (
            'Seções da resposta, separadas por vírgula. Aceita: summary (totais do recorte), '
            'breakdowns (as quebras de group_by), position (saldo e investimentos), forecast (crédito '
            'a vencer) e transactions (a lista em si). PADRÃO: summary,breakdowns,position. Peça só o '
            'que for usar: transactions é de longe a seção mais cara.'
        ),
        'controle_de_tamanho': {
            'top': 'Limita cada quebra não temporal às N maiores linhas, somando o resto numa linha "outros". Assim o total continua fechando.',
            'limit / offset': 'Tamanho e deslocamento da lista de transactions. limit=0 devolve a contagem sem as linhas.',
            'order': 'Ordem da lista: recent (padrão), oldest, largest ou smallest — largest responde "meus maiores gastos".',
        },
        'escopos_que_ignoram_o_filtro': (
            'position é sempre a posição acumulada inteira: saldo não tem período, e recortá-lo daria '
            'um número que não existe no extrato. forecast é sempre crédito a vencer daqui para a '
            'frente, mas obedece aos filtros de conta, categoria, cartão, valor e descrição. As duas '
            'seções trazem um campo "scope" repetindo isso.'
        ),
    },

    'como_ler_os_numeros': {
        'balance': 'Saldo em conta: posição acumulada, entradas menos saídas, considerando TODAS as naturezas e sem recorte de período. Só métodos liquidados (Débito e Não Se Aplica); o crédito fica fora porque ainda vai vencer.',
        'invested': 'Soma dos saldos dos investimentos (aplicado + rendido - resgatado). É posição, não fluxo.',
        'income / outcome': 'Entradas e saídas do recorte pedido. Nos padrões, só natureza REGULAR e só métodos liquidados.',
        'net': 'income - outcome do mesmo recorte. Positivo é sobra, negativo é rombo.',
        'count': 'Quantas transações compõem aquela linha. Serve para ver se um total é uma compra grande ou muitas pequenas.',
        'forecast': 'Gasto já assumido no crédito que ainda vai vencer: type="OUT", method="CREDIT", natureza REGULAR, daqui para a frente. Não se soma ao saldo — é o que sairá dele.',
        'cuidado': 'Não some balance com forecast nem income com o valor das transferências: são recortes diferentes e o mesmo dinheiro seria contado duas vezes.',
    },

    'erros': {
        'filtro_invalido': (
            'Algum parâmetro da consulta não é aceito. A mensagem diz qual é e o que ele aceita: '
            'refaça a chamada corrigida, uma vez. Não repita a mesma chamada sem mudar o que a '
            'mensagem apontou.'
        ),
        'validacao': (
            'Os dados violam uma regra de negócio ou de validação, e NADA foi gravado. A resposta '
            'traz "errors", mapeando campo para a lista de mensagens; o que não pertence a um campo '
            'específico vem em "__all__". Entenda a causa antes de tentar de novo — e se o que falta '
            'é uma informação que só o usuário tem, pergunte a ele em vez de adivinhar.'
        ),
        'ao_falar_com_o_usuario': (
            'Toda mensagem de erro é para o SEU uso. Nunca repasse ao usuário nome técnico de erro, '
            'nome de campo como a validação o identifica, JSON cru ou nome de ferramenta. Traduza '
            'para uma frase natural: o que faltou, ou que não foi possível concluir agora.'
        ),
    },
}

