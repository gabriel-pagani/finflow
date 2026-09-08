import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0004_alter_account_description_alter_category_description_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='card',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.RESTRICT, related_name='transactions', to='app.card', verbose_name='Cartão'),
        ),
    ]
