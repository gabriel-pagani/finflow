from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0005_alter_transaction_card'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='transaction',
            name='transaction_user_date_idx',
        ),
    ]
