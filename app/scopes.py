from .models import Method, Nature


# Recortes de negócio compartilhados pelas telas e pelo assistente. Mantê-los
# aqui impede que uma mudança no dashboard deixe as análises com outra semântica.
OVERVIEW_METHODS = (Method.DEBIT, Method.NOT_APPLICABLE)
FORECAST_METHODS = (Method.CREDIT,)
ANALYTIC_NATURES = (Nature.REGULAR,)
