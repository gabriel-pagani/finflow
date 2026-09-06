"""O portal de administração, dividido entre cadastro e registro financeiro.

Os módulos são importados só pelo efeito colateral: quem registra cada modelo é
o @admin.register dentro deles, e o autodiscover do Django carrega este pacote.
"""

from . import accounts, finance


__all__ = ['accounts', 'finance']
