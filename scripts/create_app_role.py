"""Cria o usuário do Postgres com que o app se conecta, sem superusuário.

Executado pelo alvo create-app-role do Makefile, num contêiner avulso do django:
ele lê o deploy/.env como está agora, com POSTGRES_APP_USER e
POSTGRES_APP_PASSWORD já preenchidos, e entra no banco como o POSTGRES_USER, o
superusuário que nasceu junto com o volume. O django no ar não é tocado e segue
com o superusuário até ser reiniciado.

O usuário novo passa a ser dono do banco e das tabelas, que é o que as
migrations precisam, e ganha CREATEDB só para os testes criarem o banco deles.
Rodar de novo é seguro: a senha é atualizada e o que já é dele continua dele.

Para voltar atrás, basta tirar as duas variáveis do .env e reiniciar: o
superusuário lê e escreve tudo, seja de quem for.
"""

import os
import sys

import psycopg2
from psycopg2 import sql

app_user = os.getenv('POSTGRES_APP_USER', '').strip()
app_password = os.getenv('POSTGRES_APP_PASSWORD', '')
admin_user = os.getenv('POSTGRES_USER', '')

if not app_user or not app_password:
    sys.exit('Preencha POSTGRES_APP_USER e POSTGRES_APP_PASSWORD no deploy/.env antes de rodar.')
if app_user == admin_user:
    sys.exit('POSTGRES_APP_USER precisa ser diferente do POSTGRES_USER, que é o superusuário.')

connection = psycopg2.connect(
    host=os.getenv('POSTGRES_HOST', 'postgres'),
    port=os.getenv('POSTGRES_PORT', '5432'),
    dbname=os.getenv('POSTGRES_DB', 'postgres'),
    user=admin_user,
    password=os.getenv('POSTGRES_PASSWORD'),
)
database = connection.info.dbname
role = sql.Identifier(app_user)
password = sql.Literal(app_password)

# Numa transação só: ou o usuário sai dono de tudo, ou nada muda.
with connection, connection.cursor() as cursor:
    cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [app_user])
    if cursor.fetchone():
        cursor.execute(sql.SQL('ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD {}').format(role, password))
    else:
        cursor.execute(sql.SQL('CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEROLE CREATEDB PASSWORD {}').format(role, password))

    cursor.execute(sql.SQL('ALTER DATABASE {} OWNER TO {}').format(sql.Identifier(database), role))

    # Num banco criado do zero o schema é do dono do banco, seja ele quem for;
    # num restaurado de dump, pode ter ficado com o superusuário.
    cursor.execute("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'")
    if cursor.fetchone()[0] != 'pg_database_owner':
        cursor.execute(sql.SQL('ALTER SCHEMA public OWNER TO {}').format(role))

    # As tabelas nasceram das migrations, rodadas pelo superusuário. Índices e
    # as sequências dos ids trocam de dono junto com a tabela.
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    for (table,) in cursor.fetchall():
        cursor.execute(sql.SQL('ALTER TABLE public.{} OWNER TO {}').format(sql.Identifier(table), role))

    # O que sobrasse com outro dono quebraria a próxima migration que mexesse
    # nele, já com o django no ar; melhor desfazer tudo aqui.
    cursor.execute(
        "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND pg_get_userbyid(relowner) <> %s",
        [app_user],
    )
    leftover = sorted(name for (name,) in cursor.fetchall())
    if leftover:
        raise SystemExit(f'Nada foi alterado: estes objetos não trocaram de dono: {", ".join(leftover)}.')

connection.close()

print(f'Usuário "{app_user}" pronto, dono do banco "{database}" e das tabelas.')
print('Suba o sistema de novo (make build-system) para o django passar a entrar com ele.')
