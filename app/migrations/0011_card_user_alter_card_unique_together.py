# Escrita à mão: o campo nasce obrigatório, e o makemigrations pediria um
# default para as linhas existentes. Não há linhas — a tabela de cartões foi
# criada na 0010 e o cadastro só passou a existir agora, com o dono junto.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('app', '0010_alter_installment_datetime_card_installment_card_and_more'),
    ]

    operations = [
        # A unicidade antiga sai primeiro: ela não cita o dono, e manter as duas
        # ao mesmo tempo impediria dois usuários de terem cartões com o mesmo
        # final na mesma conta — que é justamente o caso que o dono libera.
        migrations.AlterUniqueTogether(
            name='card',
            unique_together=set(),
        ),
        migrations.AddField(
            model_name='card',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cards', to=settings.AUTH_USER_MODEL, verbose_name='Usuário'),
            preserve_default=False,
        ),
        migrations.AlterUniqueTogether(
            name='card',
            unique_together={('user', 'account', 'last_digits')},
        ),
    ]
