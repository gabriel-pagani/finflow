import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0012_apitoken'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='api_token',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='transactions',
                to='app.apitoken',
                verbose_name='Origem API',
            ),
        ),
    ]
