"""Cria na Cloudflare o desafio das telas de entrada, na frente de tudo.

Executado pelo alvo protect-login do Makefile, num contêiner avulso do django:
ele lê o deploy/.env como está agora e fala com a API da Cloudflare pela rede.
O sistema no ar não é tocado, e nada aqui depende do Django.

O motivo de a regra viver lá e não aqui: os tetos deste repositório contam por
IP — o limit_req do nginx e o teto do pedido de conta — e o do axes conta por
usuário e IP. Quem tem uma lista de endereços recomeça todos eles em cada
endereço novo. O desafio na borda não conta nada: cada tentativa precisa
resolvê-lo antes de chegar aqui, e resolver custa mais do que trocar de IP. O
teto por usuário do app.utils.throttle é o que segura o resto.

O desafio é o administrado (managed challenge), que para o navegador de verdade
é quase sempre invisível: o cookie de liberação sai no GET da tela e o POST do
formulário passa com ele. As telas cobertas são a da senha, a do código, a do
pedido de conta e a do portal de administração — esta última é a única porta de
entrada que nem o nginx enxerga, porque o limit_req dele só vale para o /login/.

Rodar de novo é seguro: a regra é procurada pela descrição e alterada sozinha,
por endpoint próprio, sem reescrever as outras regras da zona. Para desfazer:

    make protect-login args=remove

O CLOUDFLARE_API_TOKEN precisa da permissão de editar o WAF da zona, e não é o
mesmo token do túnel.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API = 'https://api.cloudflare.com/client/v4'

# A descrição é o que identifica a regra nas próximas execuções. Mudá-la faz o
# script criar outra regra em vez de mexer na que já existe.
DESCRIPTION = 'finflow: desafio nas telas de entrada'

PHASE = 'http_request_firewall_custom'

token = os.getenv('CLOUDFLARE_API_TOKEN', '').strip()
zone = os.getenv('CLOUDFLARE_ZONE_ID', '').strip()
panel = os.getenv('ADMIN_PANEL_PATH', 'admin').strip('/')
hosts = [host.strip() for host in os.getenv('ALLOWED_HOSTS', '').split(',') if host.strip() not in ('', '*')]
remove = 'remove' in sys.argv[1:]

if not token or not zone:
    sys.exit('Preencha CLOUDFLARE_API_TOKEN e CLOUDFLARE_ZONE_ID no deploy/.env antes de rodar.')


def request(method, path, payload=None, missing_ok=False):
    body = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(f'{API}{path}', data=body, method=method, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })

    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404 and missing_ok:
            return None
        sys.exit(f'A Cloudflare recusou {method} {path}: {error.code}\n{error.read().decode(errors="replace")}')
    except urllib.error.URLError as error:
        sys.exit(f'A Cloudflare não respondeu: {error.reason}')


def expression():
    paths = ' or '.join([
        'starts_with(http.request.uri.path, "/login")',
        'starts_with(http.request.uri.path, "/access-request")',
        f'starts_with(http.request.uri.path, "/{panel}/login")',
    ])

    # Sem os hosts do .env a regra vale para a zona inteira, e uma zona pode
    # servir outro site com /login próprio.
    if hosts:
        served = ' '.join(f'"{host}"' for host in hosts)
        return f'(http.host in {{{served}}}) and ({paths})'

    return f'({paths})'


rule = {
    'description': DESCRIPTION,
    'expression': expression(),
    'action': 'managed_challenge',
    'enabled': True,
}

ruleset = request('GET', f'/zones/{zone}/rulesets/phases/{PHASE}/entrypoint', missing_ok=True)
existing = next(
    (other['id'] for other in (ruleset['result'].get('rules') or []) if other.get('description') == DESCRIPTION),
    None,
) if ruleset else None

if remove:
    if existing is None:
        sys.exit(f'Nenhuma regra com a descrição {DESCRIPTION!r} na zona: nada a remover.')

    request('DELETE', f"/zones/{zone}/rulesets/{ruleset['result']['id']}/rules/{existing}")
    sys.exit('Regra removida. As outras regras da zona seguem como estavam.')

print(f'Expressão: {rule["expression"]}')

if ruleset is None:
    # A zona ainda não tem regras personalizadas, e o conjunto nasce agora.
    request('POST', f'/zones/{zone}/rulesets', {
        'name': 'default',
        'kind': 'zone',
        'phase': PHASE,
        'rules': [rule],
    })
    print('Conjunto de regras criado com o desafio das telas de entrada.')
elif existing:
    request('PATCH', f"/zones/{zone}/rulesets/{ruleset['result']['id']}/rules/{existing}", rule)
    print('Regra que já existia atualizada no lugar.')
else:
    request('POST', f"/zones/{zone}/rulesets/{ruleset['result']['id']}/rules", rule)
    print('Regra criada no fim das regras da zona.')

print('Entre no sistema agora para conferir: se a tela de login travar, desfaça com make protect-login args=remove.')
