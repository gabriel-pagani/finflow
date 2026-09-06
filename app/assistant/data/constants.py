"""Os poucos valores fixos que filtro, agregação e posição compartilham.

Ficam num módulo só porque o assistente compara as strings que recebe: mudar o
rótulo de "sem categoria" em um lugar e não no outro faria a mesma linha ter
dois nomes conforme o eixo pelo qual ela foi pedida.
"""

from ...models import Method


# Métodos que o painel do realizado considera: dinheiro que já saiu da conta. O
# crédito fica de fora porque ainda vai vencer, e somá-lo ao saldo contaria duas
# vezes a mesma compra — uma agora, outra quando a fatura for paga. É o padrão da
# análise, não uma amarra: quem passar method= escolhe outro recorte.
SETTLED_METHODS = [Method.DEBIT, Method.NOT_APPLICABLE]

# Rótulos do que não tem registro do outro lado. Ficam aqui, e não espalhados
# pelas funções de agregação, porque o assistente compara a string que recebe.
UNCATEGORIZED = 'Categoria Não Identificada'
NO_CARD = 'Sem Cartão'
